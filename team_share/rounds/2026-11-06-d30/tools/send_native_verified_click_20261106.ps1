param([Parameter(Mandatory=$true)][string]$RequestId)
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class DNativeClick { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y); [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra); }'
[void][DNativeClick]::SetProcessDPIAware()
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskRoot='C:/Users/lenovo/Desktop/Agent挑战赛'
$taskRequest=Get-Content -LiteralPath (Join-Path $taskRoot ".session-audit/score-native-requests-20261102/$RequestId.json") -Raw | ConvertFrom-Json
$taskExpected=[IO.File]::ReadAllText($taskRequest.prompt_file)
$taskWindows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object {$_.Current.Name -eq '科研助手镜像验收总控 — DeepSeek Harness'})
if($taskWindows.Count -ne 1){throw 'Original Harness not unique'}
$taskWindow=$taskWindows[0]
$taskHeader=$taskWindow.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'科研助手镜像验收总控'))
$taskWorkspace=$taskWindow.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'Agent挑战赛'))
if($null -eq $taskHeader -or $null -eq $taskWorkspace -or $taskHeader.Current.IsOffscreen -or $taskWorkspace.Current.IsOffscreen){throw 'Original binding not visible'}
$taskEdits=@($taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)) | Where-Object {-not $_.Current.IsOffscreen -and $_.Current.Name -like '发消息或创建任务*'})
if($taskEdits.Count -ne 1){throw 'Composer not unique'}
$taskValue=$taskEdits[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
if($taskValue.Current.Value -cne $taskExpected){throw 'Not the exact unsent request; no click'}
$taskSend=$taskWindow.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'发送消息'))
if($null -eq $taskSend -or -not $taskSend.Current.IsEnabled -or $taskSend.Current.IsOffscreen){throw 'Send unavailable'}
$taskPoint=$taskSend.GetClickablePoint()
if(-not $taskWindow.Current.BoundingRectangle.Contains($taskPoint)){throw 'Click outside original window'}
$taskHandle=[IntPtr]$taskWindow.Current.NativeWindowHandle
[void][DNativeClick]::SetForegroundWindow($taskHandle)
if([DNativeClick]::GetForegroundWindow() -ne $taskHandle){throw 'Foreground not verified'}
[void][DNativeClick]::SetCursorPos([int]$taskPoint.X,[int]$taskPoint.Y)
[DNativeClick]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
[DNativeClick]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
@{request_id=$RequestId;clicked_send_once=$true;native_acknowledgement='pending';approval_clicked=$false}|ConvertTo-Json -Compress
