' koemado launcher: starts the supervisor with NO console window (window style 0).
' start.bat and the desktop/start-menu shortcuts use this. ASCII only on purpose.
Dim sh, fso, root, py
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
py = root & "\app\.venv\Scripts\python.exe"
If Not fso.FileExists(py) Then
    MsgBox "Not installed yet. Run setup.bat first.", 48, "koemado"
    WScript.Quit 1
End If
sh.CurrentDirectory = root
' same environment as start.bat (it sets PYTHONUTF8=1); children inherit it
sh.Environment("PROCESS")("PYTHONUTF8") = "1"
sh.Run """" & py & """ """ & root & "\supervisor.py"" launch", 0, False
