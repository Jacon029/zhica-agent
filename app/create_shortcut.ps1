$ProjectDir = "D:\TRAEIDE\F\thinking\zhica-agent"
$BatchFile = Join-Path $ProjectDir "run.bat"
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopDir "zhicai-Agent.lnk"

$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $BatchFile
$Shortcut.WorkingDirectory = $ProjectDir
$Shortcut.Description = "zhicai-Agent - Smart Procurement"
$Shortcut.WindowStyle = 7
$Shortcut.Save()

Write-Host "OK - Shortcut created at: $ShortcutPath"
