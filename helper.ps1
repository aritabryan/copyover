param(
    [Parameter(Mandatory)][ValidateSet('set-image', 'get-image', 'set-files', 'notify')][string]$Mode,
    [Parameter(ValueFromRemainingArguments)][string[]]$Values
)

Add-Type -AssemblyName System.Windows.Forms, System.Drawing

switch ($Mode) {
    'set-image' {
        $img = [System.Drawing.Image]::FromFile($Values[0])
        try { [System.Windows.Forms.Clipboard]::SetImage($img) } finally { $img.Dispose() }
    }
    'get-image' {
        $img = [System.Windows.Forms.Clipboard]::GetImage()
        if ($null -eq $img) { exit 1 }
        try { $img.Save($Values[0], [System.Drawing.Imaging.ImageFormat]::Png) } finally { $img.Dispose() }
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
