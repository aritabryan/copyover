param(
    [Parameter(Mandatory)][ValidateSet('set-image', 'get-image', 'set-files', 'notify')][string]$Mode,
    [Parameter(ValueFromRemainingArguments)][string[]]$Values
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

function Set-ImageClipboard {
    param([string]$ImagePath, [scriptblock]$WriteClipboard = {
        param($data)
        [System.Windows.Forms.Clipboard]::SetDataObject($data, $true, 10, 100)
    })
    $image = [System.Drawing.Image]::FromFile($ImagePath)
    $png = [IO.MemoryStream]::new()
    $bmp = [IO.MemoryStream]::new()
    $dib = $null
    try {
        if ($image.RawFormat.Guid -eq [System.Drawing.Imaging.ImageFormat]::Png.Guid) {
            $raw = [IO.File]::ReadAllBytes($ImagePath)
            $png.Write($raw, 0, $raw.Length)
        } else {
            $image.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        $png.Position = 0
        $image.Save($bmp, [System.Drawing.Imaging.ImageFormat]::Bmp)
        $bytes = $bmp.ToArray()
        # CF_DIB contains the bitmap information and pixels, without the BMP file header.
        $dib = [IO.MemoryStream]::new($bytes, 14, $bytes.Length - 14)
        $data = [System.Windows.Forms.DataObject]::new()
        $data.SetData([System.Windows.Forms.DataFormats]::Bitmap, $true, $image)
        $data.SetData('PNG', $false, $png)
        $data.SetData([System.Windows.Forms.DataFormats]::Dib, $false, $dib)
        & $WriteClipboard $data
    } finally {
        if ($null -ne $dib) { $dib.Dispose() }
        $bmp.Dispose()
        $png.Dispose()
        $image.Dispose()
    }
}

function Export-ClipboardImage {
    param($Data, [string]$ImagePath)
    if ($null -eq $Data) { return $false }
    $png = $Data.GetData('PNG', $false)
    if ($png -is [IO.MemoryStream]) {
        [IO.File]::WriteAllBytes($ImagePath, $png.ToArray())
        return $true
    }
    $image = $Data.GetData([System.Windows.Forms.DataFormats]::Bitmap, $true)
    if ($null -eq $image) { return $false }
    try {
        $image.Save($ImagePath, [System.Drawing.Imaging.ImageFormat]::Png)
        return $true
    } finally { $image.Dispose() }
}

switch ($Mode) {
    'set-image' {
        Set-ImageClipboard $Values[0]
    }
    'get-image' {
        $data = [System.Windows.Forms.Clipboard]::GetDataObject()
        if (-not (Export-ClipboardImage $data $Values[0])) { exit 1 }
    }
    'set-files' {
        $list = New-Object System.Collections.Specialized.StringCollection
        $Values | ForEach-Object { [void]$list.Add($_) }
        [System.Windows.Forms.Clipboard]::SetFileDropList($list)
    }
    'notify' {
        # A single value: base64 JSON. Passed separately, quotes or a line break in the text
        # threw off the powershell -File arguments and the notification never showed.
        $d = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Values[0])) | ConvertFrom-Json
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml('<toast><visual><binding template="ToastGeneric"/></visual></toast>')
        $toast = $xml.SelectSingleNode('/toast')
        $binding = $xml.SelectSingleNode('//binding')
        # Title, text and a note («Already on the clipboard»); they go in as text nodes, never as XML.
        foreach ($line in @($d.title, $d.text, $d.note)) {
            if ($line) {
                $node = $xml.CreateElement('text')
                [void]$node.AppendChild($xml.CreateTextNode($line))
                [void]$binding.AppendChild($node)
            }
        }
        # Tapping the notification or a button opens its address through a protocol: Windows opens it, no process listening.
        # Only http(s) (a received link) or copyover-notice: (open a received file, see windows_system.py).
        function Test-SafeUri($address) {
            $uri = $null
            [Uri]::TryCreate($address, [UriKind]::Absolute, [ref]$uri) -and
                ($uri.Scheme -eq 'http' -or $uri.Scheme -eq 'https' -or $uri.Scheme -eq 'copyover-notice')
        }
        if ($d.launch -and (Test-SafeUri $d.launch)) {
            $toast.SetAttribute('activationType', 'protocol')
            $toast.SetAttribute('launch', $d.launch)
        }
        $actions = $xml.CreateElement('actions')
        foreach ($b in @($d.buttons)) {
            if ($b -and (Test-SafeUri $b[1])) {
                $button = $xml.CreateElement('action')
                $button.SetAttribute('content', $b[0])
                $button.SetAttribute('activationType', 'protocol')
                $button.SetAttribute('arguments', $b[1])
                [void]$actions.AppendChild($button)
            }
        }
        if ($actions.HasChildNodes()) { [void]$toast.AppendChild($actions) }
        # CopyOver's ID (registered by windows_system.py); without it, PowerShell's.
        $app = if ($d.app) { $d.app } else { '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe' }
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show(
            [Windows.UI.Notifications.ToastNotification]::new($xml))
    }
}
exit 0
