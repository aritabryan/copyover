//! HTTPS to the linked PC, trusting only the certificate authority stored when it was linked.
//!
//! Each PC creates its own CA and signs its server certificate with it. The hostname is not
//! checked because the PC is reached by whatever IP it has today; the chain is still validated
//! against the pinned CA (signature, dates, key usage and the CA's name constraints), and only
//! that PC holds the CA's private key.
use crate::Failure;
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::client::WebPkiServerVerifier;
use rustls::crypto::{ring, CryptoProvider};
use rustls::pki_types::pem::PemObject;
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{
    CertificateError, ClientConfig, ClientConnection, DigitallySignedStruct, Error, RootCertStore,
    SignatureScheme, StreamOwned,
};
use std::error::Error as _;
use std::fmt;
use std::io::{self, Read, Write};
use std::net::TcpStream;
use std::sync::Arc;

const MAX_CA_PEM: usize = 8192;

fn provider() -> Arc<CryptoProvider> {
    Arc::new(ring::default_provider())
}

fn invalid_ca() -> Failure {
    Failure::new("config", "Link your PC again.")
}

/// Accepts a chain only if it leads to the pinned CA; a name mismatch alone is not an error.
#[derive(Debug)]
pub(crate) struct PinnedCa {
    inner: Arc<WebPkiServerVerifier>,
}

impl PinnedCa {
    pub(crate) fn new(ca_pem: &str) -> Result<Self, Failure> {
        if ca_pem.is_empty() || ca_pem.len() > MAX_CA_PEM {
            return Err(invalid_ca());
        }
        let mut certificates = CertificateDer::pem_slice_iter(ca_pem.as_bytes());
        let ca = certificates
            .next()
            .ok_or_else(invalid_ca)?
            .map_err(|_| invalid_ca())?;
        if certificates.next().is_some() {
            return Err(invalid_ca());
        }
        let mut roots = RootCertStore::empty();
        roots.add(ca).map_err(|_| invalid_ca())?;
        let inner = WebPkiServerVerifier::builder_with_provider(Arc::new(roots), provider())
            .build()
            .map_err(|_| invalid_ca())?;
        Ok(Self { inner })
    }
}

impl ServerCertVerifier for PinnedCa {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        intermediates: &[CertificateDer<'_>],
        server_name: &ServerName<'_>,
        ocsp_response: &[u8],
        now: UnixTime,
    ) -> Result<ServerCertVerified, Error> {
        // WebPkiServerVerifier checks the chain first and the name last, so a name error means
        // the chain already validated against the pinned CA.
        match self.inner.verify_server_cert(
            end_entity,
            intermediates,
            server_name,
            ocsp_response,
            now,
        ) {
            Err(Error::InvalidCertificate(
                CertificateError::NotValidForName | CertificateError::NotValidForNameContext { .. },
            )) => Ok(ServerCertVerified::assertion()),
            other => other,
        }
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, Error> {
        self.inner.verify_tls12_signature(message, cert, dss)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, Error> {
        self.inner.verify_tls13_signature(message, cert, dss)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.inner.supported_verify_schemes()
    }
}

/// TLS connector for ureq that trusts only the pinned CA.
pub(crate) struct Connector(Arc<ClientConfig>);

impl Connector {
    pub(crate) fn new(ca_pem: &str) -> Result<Self, Failure> {
        let verifier = PinnedCa::new(ca_pem)?;
        let config = ClientConfig::builder_with_provider(provider())
            .with_protocol_versions(&[&rustls::version::TLS13, &rustls::version::TLS12])
            .map_err(|_| invalid_ca())?
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(verifier))
            .with_no_client_auth();
        Ok(Self(Arc::new(config)))
    }
}

impl ureq::TlsConnector for Connector {
    fn connect(
        &self,
        dns_name: &str,
        mut io: Box<dyn ureq::ReadWrite>,
    ) -> Result<Box<dyn ureq::ReadWrite>, ureq::Error> {
        let host = dns_name.trim_start_matches('[').trim_end_matches(']');
        let name = ServerName::try_from(host)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "server name"))?
            .to_owned();
        let mut connection = ClientConnection::new(self.0.clone(), name)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        // Handshake now: nothing (not even the key header) is written before the PC is verified.
        while connection.is_handshaking() {
            connection.complete_io(&mut io)?;
        }
        Ok(Box::new(TlsStream(StreamOwned::new(connection, io))))
    }
}

struct TlsStream(StreamOwned<ClientConnection, Box<dyn ureq::ReadWrite>>);

impl ureq::ReadWrite for TlsStream {
    fn socket(&self) -> Option<&TcpStream> {
        self.0.get_ref().socket()
    }
}

impl Read for TlsStream {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        self.0.read(buf)
    }
}

impl Write for TlsStream {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.0.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.0.flush()
    }
}

impl fmt::Debug for TlsStream {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_tuple("TlsStream").finish()
    }
}

/// True when the PC answered with a certificate that does not lead to the pinned CA.
pub(crate) fn certificate_rejected(transport: &ureq::Transport) -> bool {
    let mut current = transport.source();
    while let Some(error) = current {
        let rustls_error = error.downcast_ref::<Error>().or_else(|| {
            error
                .downcast_ref::<io::Error>()
                .and_then(|io| io.get_ref())
                .and_then(|inner| inner.downcast_ref::<Error>())
        });
        if matches!(rustls_error, Some(Error::InvalidCertificate(_))) {
            return true;
        }
        current = error.source();
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    const CA: &str = include_str!("../tests/fixtures/ca.pem");
    const SERVER: &str = include_str!("../tests/fixtures/servidor.pem");
    const OTHER_CA: &str = include_str!("../tests/fixtures/otra_ca.pem");
    const IMPOSTOR: &str = include_str!("../tests/fixtures/impostor.pem");
    // Fixtures made with certificados.py: the server certificate is valid from 1793396359 to 1827697459.
    const VALID: u64 = 1_793_400_000;

    fn der(pem: &str) -> CertificateDer<'static> {
        CertificateDer::from_pem_slice(pem.as_bytes()).unwrap()
    }

    fn verify(ca: &str, leaf: &str, name: &str, at: u64) -> Result<ServerCertVerified, Error> {
        let ca_der = der(ca);
        PinnedCa::new(ca).unwrap().verify_server_cert(
            &der(leaf),
            &[ca_der],
            &ServerName::try_from(name.to_owned()).unwrap(),
            &[],
            UnixTime::since_unix_epoch(Duration::from_secs(at)),
        )
    }

    #[test]
    fn accepts_the_pinned_ca_by_name_or_any_address() {
        assert!(verify(CA, SERVER, "fixture-pc.local", VALID).is_ok());
        assert!(verify(CA, SERVER, "192.168.1.20", VALID).is_ok());
        // The IP changed since the certificate was made: still the same PC.
        assert!(verify(CA, SERVER, "10.0.0.7", VALID).is_ok());
        assert!(verify(CA, SERVER, "other-name.local", VALID).is_ok());
    }

    #[test]
    fn rejects_a_server_signed_by_another_ca() {
        assert!(matches!(
            verify(CA, IMPOSTOR, "fixture-pc.local", VALID),
            Err(Error::InvalidCertificate(_))
        ));
        assert!(matches!(
            verify(OTHER_CA, SERVER, "fixture-pc.local", VALID),
            Err(Error::InvalidCertificate(_))
        ));
        // Sending the pinned CA in the chain does not help an impostor.
        let result = PinnedCa::new(CA).unwrap().verify_server_cert(
            &der(IMPOSTOR),
            &[der(CA), der(OTHER_CA)],
            &ServerName::try_from("fixture-pc.local").unwrap(),
            &[],
            UnixTime::since_unix_epoch(Duration::from_secs(VALID)),
        );
        assert!(matches!(result, Err(Error::InvalidCertificate(_))));
    }

    #[test]
    fn rejects_the_ca_itself_and_expired_certificates() {
        assert!(verify(CA, CA, "fixture-pc.local", VALID).is_err());
        assert!(matches!(
            verify(CA, SERVER, "fixture-pc.local", 1_827_697_459 + 60),
            Err(Error::InvalidCertificate(
                CertificateError::Expired | CertificateError::ExpiredContext { .. }
            ))
        ));
    }

    #[test]
    fn rejects_malformed_pins() {
        assert!(PinnedCa::new("").is_err());
        assert!(PinnedCa::new("not a certificate").is_err());
        assert!(PinnedCa::new(&format!("{CA}{OTHER_CA}")).is_err());
        assert!(PinnedCa::new(&"A".repeat(MAX_CA_PEM + 1)).is_err());
    }
}
