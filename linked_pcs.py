"""Public views of the PC links (never include credentials) and each one's stored CA."""

from i18n import t


def with_alias(cfg, nearby):
    info = cfg.get('pcs_info', {})
    return [dict(pc, name=info.get(pc.get('id'), {}).get('alias') or pc['name'])
            for pc in nearby]


def list_all(cfg, nearby):
    available = {pc['id']: pc for pc in nearby}
    result = []
    for peer_id in cfg.get('pcs', {}):
        saved = cfg.get('pcs_info', {}).get(peer_id, {})
        pc = available.get(peer_id, {})
        name = saved.get('alias') or pc.get('name') or saved.get('name') or f'PC {peer_id[:8]}'
        result.append({'id': peer_id, 'name': name, 'available': bool(pc),
                          'host': pc.get('host', ''), 'ip': pc.get('ip', ''),
                          'encrypted': bool(saved.get('ca'))})
    return result


def save_ca(cfg, peer_id, ca_pem):
    """When pairing with protocol 2: the other PC's CA and its fingerprint. Without a CA (protocol 1) it's
    deleted, so a CA from an earlier link never stays pinned."""
    info = cfg.setdefault('pcs_info', {}).setdefault(peer_id, {})
    if ca_pem:
        import certificates
        info['ca'] = ca_pem
        info['ca_fingerprint'] = certificates.pem_fingerprint(ca_pem)
    else:
        info.pop('ca', None)
        info.pop('ca_fingerprint', None)


def ca_of(cfg, peer_id):
    """The stored CA of a paired PC, or None if it was linked without encryption (older CopyOver). If the CA
    doesn't match its fingerprint (config edited by hand) it isn't used: ValueError is raised."""
    info = cfg.get('pcs_info', {}).get(peer_id, {})
    ca = info.get('ca')
    if not ca:
        return None
    import certificates
    if certificates.pem_fingerprint(ca) != info.get('ca_fingerprint'):
        raise ValueError(t('The saved CA of this PC doesn\'t match its fingerprint'))
    return ca


def encrypted_ids(cfg):
    return {peer for peer in cfg.get('pcs', {}) if cfg.get('pcs_info', {}).get(peer, {}).get('ca')}


def remember_names(cfg, nearby):
    changed = False
    for pc in nearby:
        if pc['id'] not in cfg.get('pcs', {}):
            continue
        info = cfg.setdefault('pcs_info', {}).setdefault(pc['id'], {})
        if info.get('name') != pc['name']:
            info['name'] = pc['name']
            changed = True
    return changed
