#Requires -Version 5.1
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\.env"

function Get-EnvFileValue([string]$Name) {
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        return $null
    }

    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        $match = [regex]::Match($line, "^\s*" + [regex]::Escape($Name) + "\s*=\s*(.*)$")
        if ($match.Success) {
            $value = $match.Groups[1].Value.Trim()
            if ($value.Length -ge 2) {
                if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                    ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
            }
            return $value
        }
    }

    return $null
}

function Get-ConfiguredValue([string]$Name) {
    $processValue = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue.Trim().Trim([char]'"').Trim([char]"'")
    }
    return Get-EnvFileValue $Name
}

function New-AccessLabel(
    [string]$Text,
    [int]$X,
    [int]$Y,
    [int]$Width,
    [int]$Height,
    [int]$FontSize = 10,
    [bool]$Bold = $false,
    [System.Drawing.Color]$Color
) {
    if (-not $PSBoundParameters.ContainsKey("Color")) {
        $Color = [System.Drawing.Color]::FromArgb(222, 229, 240)
    }
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object System.Drawing.Point($X, $Y)
    $label.Size = New-Object System.Drawing.Size($Width, $Height)
    $label.ForeColor = $Color
    if ($Bold) {
        $label.Font = New-Object System.Drawing.Font("Segoe UI", $FontSize, [System.Drawing.FontStyle]::Bold)
    } else {
        $label.Font = New-Object System.Drawing.Font("Segoe UI", $FontSize, [System.Drawing.FontStyle]::Regular)
    }
    $form.Controls.Add($label)
    return $label
}

function New-AccessButton(
    [string]$Text,
    [int]$X,
    [int]$Y,
    [int]$Width,
    [scriptblock]$OnClick
) {
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $Text
    $button.Location = New-Object System.Drawing.Point($X, $Y)
    $button.Size = New-Object System.Drawing.Size($Width, 34)
    $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(61, 73, 94)
    $button.BackColor = [System.Drawing.Color]::FromArgb(31, 40, 56)
    $button.ForeColor = [System.Drawing.Color]::FromArgb(244, 246, 250)
    $button.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
    $button.Add_Click($OnClick)
    $form.Controls.Add($button)
    return $button
}

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    $message = "The local configuration file was not found:" + [Environment]::NewLine + $EnvFile + [Environment]::NewLine + [Environment]::NewLine + "Start Edit Factory once with START-ALL.bat to create it."
    [System.Windows.Forms.MessageBox]::Show(
        $message,
        "Edit Factory V3 — Access Details",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    ) | Out-Null
    exit 1
}

$dashboardUsers = Get-ConfiguredValue "AIVF_DASHBOARD_USERS"
$sharedTokenEnabled = [string]::IsNullOrWhiteSpace($dashboardUsers)
$dashboardToken = Get-ConfiguredValue "AIVF_DASHBOARD_TOKEN"
$adminPanelCode = Get-ConfiguredValue "AIVF_ADMIN_PANEL_CODE"

$portValue = Get-ConfiguredValue "AIVF_DASHBOARD_PORT"
if ([string]::IsNullOrWhiteSpace($portValue)) {
    $portValue = "5000"
}
if ($portValue -notmatch '^\d{1,5}$' -or [int]$portValue -lt 1 -or [int]$portValue -gt 65535) {
    $portValue = "5000"
}
$BaseUrl = "http://127.0.0.1:$([int]$portValue)"

$form = New-Object System.Windows.Forms.Form
$form.Text = "Edit Factory V3 — Access Details"
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ShowInTaskbar = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(12, 16, 24)
$form.ForeColor = [System.Drawing.Color]::FromArgb(244, 246, 250)
$form.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$form.ClientSize = New-Object System.Drawing.Size(700, 555)

$null = New-AccessLabel "EDIT FACTORY  /  ACCESS DETAILS" 28 22 640 34 19 $true ([System.Drawing.Color]::FromArgb(255, 154, 139))
$null = New-AccessLabel "Your dashboard credentials stay on this PC. They are never printed to the command window." 30 63 640 35 9 $false ([System.Drawing.Color]::FromArgb(155, 168, 188))

$null = New-AccessLabel "DASHBOARD SIGN-IN TOKEN" 30 112 500 24 10 $true
$tokenBox = New-Object System.Windows.Forms.TextBox
$tokenBox.Location = New-Object System.Drawing.Point(30, 141)
$tokenBox.Size = New-Object System.Drawing.Size(490, 34)
$tokenBox.ReadOnly = $true
$tokenBox.UseSystemPasswordChar = $true
$tokenBox.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$tokenBox.BackColor = [System.Drawing.Color]::FromArgb(23, 30, 43)
$tokenBox.ForeColor = [System.Drawing.Color]::FromArgb(244, 246, 250)
$tokenBox.Font = New-Object System.Drawing.Font("Consolas", 10)
if ($sharedTokenEnabled -and -not [string]::IsNullOrWhiteSpace($dashboardToken)) {
    $tokenBox.Text = $dashboardToken
}
$form.Controls.Add($tokenBox)

$showTokenButton = New-AccessButton "Show" 530 141 70 {
    param($sender, $eventArgs)
    if ($tokenBox.UseSystemPasswordChar) {
        $tokenBox.UseSystemPasswordChar = $false
        $sender.Text = "Hide"
    } else {
        $tokenBox.UseSystemPasswordChar = $true
        $sender.Text = "Show"
    }
}
$copyTokenButton = New-AccessButton "Copy" 608 141 62 {
    param($sender, $eventArgs)
    if (-not [string]::IsNullOrWhiteSpace($tokenBox.Text)) {
        [System.Windows.Forms.Clipboard]::SetText($tokenBox.Text)
        $sender.Text = "Copied"
    }
}

if (-not $sharedTokenEnabled) {
    $tokenBox.Enabled = $false
    $showTokenButton.Enabled = $false
    $copyTokenButton.Enabled = $false
    $null = New-AccessLabel "Named dashboard accounts are enabled. Shared-token sign-in is disabled; use the token assigned to your account." 30 184 640 44 9 $false ([System.Drawing.Color]::FromArgb(255, 196, 130))
} elseif ([string]::IsNullOrWhiteSpace($dashboardToken)) {
    $tokenBox.Enabled = $false
    $showTokenButton.Enabled = $false
    $copyTokenButton.Enabled = $false
    $null = New-AccessLabel "No dashboard token was found. Start Edit Factory with START-ALL.bat to regenerate local configuration." 30 184 640 44 9 $false ([System.Drawing.Color]::FromArgb(255, 196, 130))
} else {
    $null = New-AccessLabel "Use this token on the dashboard sign-in page. Click Show to reveal it or Copy to paste it into login." 30 184 640 35 9 $false ([System.Drawing.Color]::FromArgb(155, 168, 188))
}

$null = New-AccessLabel "ADMIN CONSOLE UNLOCK CODE" 30 255 500 24 10 $true
$adminBox = New-Object System.Windows.Forms.TextBox
$adminBox.Location = New-Object System.Drawing.Point(30, 284)
$adminBox.Size = New-Object System.Drawing.Size(490, 34)
$adminBox.ReadOnly = $true
$adminBox.UseSystemPasswordChar = $true
$adminBox.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$adminBox.BackColor = [System.Drawing.Color]::FromArgb(23, 30, 43)
$adminBox.ForeColor = [System.Drawing.Color]::FromArgb(244, 246, 250)
$adminBox.Font = New-Object System.Drawing.Font("Consolas", 10)
if (-not [string]::IsNullOrWhiteSpace($adminPanelCode)) {
    $adminBox.Text = $adminPanelCode
}
$form.Controls.Add($adminBox)

$showAdminButton = New-AccessButton "Show" 530 284 70 {
    param($sender, $eventArgs)
    if ($adminBox.UseSystemPasswordChar) {
        $adminBox.UseSystemPasswordChar = $false
        $sender.Text = "Hide"
    } else {
        $adminBox.UseSystemPasswordChar = $true
        $sender.Text = "Show"
    }
}
$copyAdminButton = New-AccessButton "Copy" 608 284 62 {
    param($sender, $eventArgs)
    if (-not [string]::IsNullOrWhiteSpace($adminBox.Text)) {
        [System.Windows.Forms.Clipboard]::SetText($adminBox.Text)
        $sender.Text = "Copied"
    }
}

if ([string]::IsNullOrWhiteSpace($adminPanelCode)) {
    $adminBox.Enabled = $false
    $showAdminButton.Enabled = $false
    $copyAdminButton.Enabled = $false
    $null = New-AccessLabel "No admin unlock code is configured for this install." 30 327 640 24 9 $false ([System.Drawing.Color]::FromArgb(155, 168, 188))
} else {
    $null = New-AccessLabel "To open the admin console, sign in as an admin and click the small V3 badge five times quickly, then enter this code." 30 327 640 43 9 $false ([System.Drawing.Color]::FromArgb(155, 168, 188))
}

$null = New-AccessLabel "This window reads the local .env file only. It does not send credentials to an online service." 30 400 640 25 9 $false ([System.Drawing.Color]::FromArgb(117, 132, 154))

$openLoginButton = New-AccessButton "Open sign-in page" 30 477 180 {
    param($sender, $eventArgs)
    Start-Process -FilePath "$BaseUrl/login"
}
$closeButton = New-AccessButton "Close" 590 477 80 {
    param($sender, $eventArgs)
    $form.Close()
}
$openLoginButton.BackColor = [System.Drawing.Color]::FromArgb(237, 81, 61)
$openLoginButton.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(237, 81, 61)

$null = New-AccessLabel "You can reopen this window any time with SHOW-LOCAL-CREDENTIALS.bat." 30 523 640 22 9 $false ([System.Drawing.Color]::FromArgb(117, 132, 154))

$form.Add_FormClosed({
    $tokenBox.Clear()
    $adminBox.Clear()
})

try {
    $form.ShowDialog() | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        "The access details window could not be opened. Check that Edit Factory is installed correctly, then try SHOW-LOCAL-CREDENTIALS.bat again.",
        "Edit Factory V3 — Access Details",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}
