param([Parameter(Mandatory=$true)][string]$RequestId)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ScoreForeground { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd); }'
$taskRoot='C:\Users\lenovo\Desktop\Agent挑战赛'
$taskRequest=Get-Content -LiteralPath (Join-Path $taskRoot ".session-audit/score-native-requests-20261102/$RequestId.json") -Raw | ConvertFrom-Json
$taskCandidates=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object {$_.Current.ClassName -eq 'Chrome_WidgetWin_1' -and $_.Current.Name -eq '科研助手镜像验收总控 — DeepSeek Harness'})
if($taskCandidates.Count -eq 0){
    # Only restore the already running, known application. Do not create a
    # profile/workspace or select a different conversation.
    $taskExecutable='D:\deepseek\DeepSeek Harness.exe'
    $taskExisting=@(Get-Process -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $taskExecutable})
    if($taskExisting.Count -eq 0){throw 'Original Harness process is unavailable; no launch or send'}
    Start-Process -FilePath $taskExecutable -WindowStyle Hidden
    Start-Sleep -Seconds 2
    $taskCandidates=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object {$_.Current.ClassName -eq 'Chrome_WidgetWin_1' -and $_.Current.Name -eq '科研助手镜像验收总控 — DeepSeek Harness'})
}
if($taskCandidates.Count -ne 1){throw 'Original native Harness main window is not unique'}
$taskWindow=[IntPtr]$taskCandidates[0].Current.NativeWindowHandle
$taskElement=[System.Windows.Automation.AutomationElement]::FromHandle($taskWindow)
$taskElement.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).SetWindowVisualState([System.Windows.Automation.WindowVisualState]::Normal)
$taskBounds=$taskElement.Current.BoundingRectangle
for($taskRenderAttempt=0;$taskRenderAttempt -lt 10;$taskRenderAttempt++){
    $taskHeader=$taskElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'科研助手镜像验收总控'))
    $taskWorkspace=$taskElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'Agent挑战赛'))
    if($null -ne $taskHeader -and -not $taskHeader.Current.IsOffscreen -and $null -ne $taskWorkspace -and -not $taskWorkspace.Current.IsOffscreen){break}
    Start-Sleep -Milliseconds 300
}
if($null -eq $taskHeader -or $taskHeader.Current.IsOffscreen -or $null -eq $taskWorkspace -or $taskWorkspace.Current.IsOffscreen){throw 'Required native workspace and conversation are not visible'}
$taskHeaderBounds=$taskHeader.Current.BoundingRectangle
if($taskHeaderBounds.Top-$taskBounds.Top -gt 180 -or $taskHeaderBounds.Left-$taskBounds.Left -lt 250){throw 'Conversation name is not in the active main header'}
$taskEditors=$taskElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit))
$taskVisible=@($taskEditors | Where-Object {-not $_.Current.IsOffscreen -and $_.Current.Name -like '发消息或创建任务*'})
if($taskVisible.Count -ne 1){throw 'No unique native composer'}
$taskMessage=[IO.File]::ReadAllText($taskRequest.prompt_file)
$taskValue=$taskVisible[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
if($taskValue.Current.Value.Length -and $taskValue.Current.Value -cne $taskMessage){throw 'Unrelated draft exists; stopped'}
$taskClipboard=$null
$taskClipboardUsed=$false
try {
    # Request only the already verified Harness window; never send keyboard
    # input unless both foreground handle and native editor focus agree.
    [void][ScoreForeground]::SetForegroundWindow($taskWindow)
    # A native application menu can retain keyboard capture while the HTML
    # textarea reports focus. Close only this known menu, never an approval.
    $taskAbout=$taskElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'关于 DeepSeek Harness'))
    if($null -ne $taskAbout -and -not $taskAbout.Current.IsOffscreen){
        if([ScoreForeground]::GetForegroundWindow() -ne $taskWindow){throw 'Foreground changed before closing own app menu'}
        [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
        Start-Sleep -Milliseconds 300
    }
    $taskVisible[0].SetFocus()
    Start-Sleep -Milliseconds 300
    if(-not $taskVisible[0].Current.HasKeyboardFocus -or [ScoreForeground]::GetForegroundWindow() -ne $taskWindow){throw 'Native editor did not receive foreground keyboard focus; no typing'}
    if($taskValue.Current.Value -cne $taskMessage){
        if($taskValue.Current.Value.Length){throw 'Composer changed before write; no overwrite'}
        try {$taskValue.SetValue($taskMessage)} catch {
            if($taskValue.Current.Value.Length){throw 'Composer changed during accessibility write; stopped'}
        }
        for($taskReadAttempt=0;$taskReadAttempt -lt 5 -and $taskValue.Current.Value -cne $taskMessage;$taskReadAttempt++){Start-Sleep -Milliseconds 200}
        # A controlled browser textarea can accept SetValue without emitting an
        # input event, then revert to empty. Paste only into that still-empty,
        # freshly focused original composer; never overwrite partial text.
        if($taskValue.Current.Value.Length -eq 0){
            if(-not $taskVisible[0].Current.HasKeyboardFocus -or [ScoreForeground]::GetForegroundWindow() -ne $taskWindow){throw 'Composer focus changed before paste; no typing'}
            $taskClipboard=[System.Windows.Forms.Clipboard]::GetDataObject()
            $taskClipboardUsed=$true
            [System.Windows.Forms.Clipboard]::SetText($taskMessage)
            [System.Windows.Forms.SendKeys]::SendWait('^v')
        }
        for($taskReadAttempt=0;$taskReadAttempt -lt 10 -and $taskValue.Current.Value -cne $taskMessage;$taskReadAttempt++){Start-Sleep -Milliseconds 200}
        if($taskClipboardUsed -and $taskValue.Current.Value.Length -eq 0){
            if(-not $taskVisible[0].Current.HasKeyboardFocus -or [ScoreForeground]::GetForegroundWindow() -ne $taskWindow -or [System.Windows.Forms.Clipboard]::GetText() -cne $taskMessage){throw 'Paste target or clipboard changed; no further typing'}
            [System.Windows.Forms.SendKeys]::SendWait('+{INSERT}')
            for($taskReadAttempt=0;$taskReadAttempt -lt 10 -and $taskValue.Current.Value -cne $taskMessage;$taskReadAttempt++){Start-Sleep -Milliseconds 200}
        }
    }
    if($taskValue.Current.Value -cne $taskMessage){throw 'Composer readback differs; not sent'}
    $taskSend=$taskElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'发送消息'))
    if($null -eq $taskSend -or -not $taskSend.Current.IsEnabled -or $taskSend.Current.IsOffscreen){throw 'Native send unavailable'}
    $taskSend.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    @{submitted=$true;request_id=$RequestId;main_header_verified=$true;workspace_visible=$true;clipboard_logged=$false}|ConvertTo-Json -Compress
} finally {
    if($taskClipboardUsed){if($null -ne $taskClipboard){[System.Windows.Forms.Clipboard]::SetDataObject($taskClipboard,$true)}else{[System.Windows.Forms.Clipboard]::Clear()}}
}
