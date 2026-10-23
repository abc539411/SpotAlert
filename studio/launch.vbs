' SpotAlert Studio — desktop shortcut launcher.
' Starts run.ps1 hidden if Studio isn't already serving, then opens the browser.
Set objShell = CreateObject("WScript.Shell")
AppDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
Url = "http://127.0.0.1:5000/"

Function IsRunning()
    IsRunning = False
    Dim objHTTP : Set objHTTP = CreateObject("MSXML2.ServerXMLHTTP")
    On Error Resume Next
    objHTTP.Open "GET", Url, False
    objHTTP.Send
    If Err.Number = 0 Then IsRunning = (objHTTP.Status > 0)
    On Error GoTo 0
End Function

If Not IsRunning() Then
    objShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & AppDir & "\run.ps1""", 0, False
    ' run.ps1 does a pip install first, so wait for the server rather than a fixed sleep
    Dim i
    For i = 1 To 60
        WScript.Sleep 1000
        If IsRunning() Then Exit For
    Next
End If

objShell.Run Url
