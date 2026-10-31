param([string]$Stage='precheck',[int]$Round=0,[switch]$Send)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskRoot='C:\Users\lenovo\Desktop\Agent挑战赛'
$taskMeta=Get-Content -LiteralPath (Join-Path $taskRoot '.session-audit/p0-ce-real-study-20261031.json') -Raw | ConvertFrom-Json
$taskMessage=[System.IO.File]::ReadAllText((Join-Path $taskMeta.directory "native-$Stage-$Round-prompt.txt"))
$taskState=Get-Content -LiteralPath (Join-Path $taskRoot '.session-audit/p0-harness-desktop-state-20261031.json') -Raw | ConvertFrom-Json
$taskElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new([long]$taskState.harness_window))
$taskCondition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)
$taskEditors=$taskElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,$taskCondition)
$taskVisible=@($taskEditors | Where-Object { -not $_.Current.IsOffscreen -and $_.Current.Name -like '发消息或创建任务*' })
if($taskVisible.Count -ne 1){throw 'No unique native message editor'}
$taskPrevious=[System.Windows.Forms.Clipboard]::GetDataObject()
$taskResult=@{submitted=$false; clipboard_content_logged=$false}
try {
    $taskValue=$taskVisible[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if($taskValue.Current.Value.Length -and $taskValue.Current.Value -cne $taskMessage){throw 'Unrelated draft exists; no changes made'}
    if($taskValue.Current.Value -cne $taskMessage){
        [System.Windows.Forms.Clipboard]::SetText($taskMessage)
        $taskVisible[0].SetFocus()
        [System.Windows.Forms.SendKeys]::SendWait('^v')
        Start-Sleep -Milliseconds 500
    }
    $taskResult.input_matches=$taskValue.Current.Value -ceq $taskMessage
    $taskResult.characters=$taskValue.Current.Value.Length
    if($Send){
        if(-not $taskResult.input_matches){throw 'Native input did not match; not sending'}
        $taskButton=$taskElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'发送消息'))
        if($null -eq $taskButton -or $taskButton.Current.IsOffscreen -or -not $taskButton.Current.IsEnabled){throw 'Send button unavailable'}
        $taskButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        $taskResult.submitted=$true
    }
} finally {
    if($null -ne $taskPrevious){[System.Windows.Forms.Clipboard]::SetDataObject($taskPrevious,$true)}
    else{[System.Windows.Forms.Clipboard]::Clear()}
    $taskResult.clipboard_restored=$true
}
$taskResult | ConvertTo-Json -Compress
