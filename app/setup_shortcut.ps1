<#
.SYNOPSIS
    智采-Agent 桌面快捷方式创建脚本
.DESCRIPTION
    在桌面上创建一个快捷方式，双击即可在浏览器中打开智采-Agent
#>

$ErrorActionPreference = "Stop"

# 项目路径
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchFile = Join-Path $ProjectDir "run.bat"
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopDir "智采-Agent.lnk"

Write-Host ""
Write-Host "╔════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  智采-Agent · 快捷方式创建工具              ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 创建快捷方式
$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)

$Shortcut.TargetPath = $BatchFile
$Shortcut.WorkingDirectory = $ProjectDir
$Shortcut.Description = "智采-Agent · 企业采购智能助手"
$Shortcut.IconLocation = "shell32.dll,46"  # 使用系统图标（购物袋图标）

# 设置启动方式：最小化命令行窗口
$Shortcut.WindowStyle = 7  # 最小化窗口

$Shortcut.Save()

Write-Host "[√] 快捷方式已创建: $ShortcutPath" -ForegroundColor Green
Write-Host ""

# 询问是否设置 API Key
$setApiKey = Read-Host "是否现在设置 Anthropic API Key？(y/n)"
if ($setApiKey -eq "y" -or $setApiKey -eq "Y") {
    $apiKey = Read-Host "请输入 API Key"
    if ($apiKey) {
        [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $apiKey, "User")
        Write-Host "[√] API Key 已设置为用户环境变量" -ForegroundColor Green
    }
}

# 询问是否设置 Tavily API Key
$setTavily = Read-Host "是否设置 Tavily Search API Key？(y/n)"
if ($setTavily -eq "y" -or $setTavily -eq "Y") {
    $tavilyKey = Read-Host "请输入 Tavily API Key"
    if ($tavilyKey) {
        [Environment]::SetEnvironmentVariable("TAVILY_API_KEY", $tavilyKey, "User")
        Write-Host "[√] Tavily API Key 已设置为用户环境变量" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  配置完成！双击桌面上的「智采-Agent」即可启动" -ForegroundColor Yellow
Write-Host "  浏览器将自动打开 http://localhost:8501" -ForegroundColor Yellow
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# 询问是否立即启动
$launch = Read-Host "是否立即启动？(y/n)"
if ($launch -eq "y" -or $launch -eq "Y") {
    Start-Process -FilePath $ShortcutPath
}
