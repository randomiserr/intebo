' ============================================================
' Intebo - tichy launcher (bez terminalu)
' ============================================================
' Spousti server na pozadi a otevre prohlizec.
' Pro prvni instalaci a zmenu konfigurace pouzijte start.bat.
'
' Server bezi na pozadi - zastavi se zavrenim "python.exe" v Task Manageru,
' nebo restartem PC.
' ============================================================

Option Explicit

Dim fso, shell, scriptDir, configPath, logPath
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
configPath = scriptDir & "\config.ini"
logPath = scriptDir & "\server.log"

' --- 1. Pokud config.ini neexistuje, presmeruj na start.bat ---
If Not fso.FileExists(configPath) Then
    MsgBox "Aplikace jeste nebyla nakonfigurovana." & vbCrLf & vbCrLf & _
           "Spustte nejdrive start.bat, ktery pripravi config.ini.", _
           vbInformation, "Intebo - prvni spusteni"
    WScript.Quit
End If

' --- 2. Precti host a port z config.ini ---
Dim host, port
host = ReadIni(configPath, "host", "localhost")
port = ReadIni(configPath, "port", "8000")

' --- 3. Kontrola, jestli uz server nebezi (port obsazen) ---
If IsPortOpen(host, port) Then
    ' Server uz bezi - jen otevri prohlizec
    shell.Run "http://" & host & ":" & port, 1, False
    WScript.Quit
End If

' --- 4. Spust server skryte ---
shell.CurrentDirectory = scriptDir
' Vystup serveru jde do server.log pro diagnostiku
shell.Run "cmd /c python -m uvicorn app:app --host " & host & " --port " & port & " > """ & logPath & """ 2>&1", 0, False

' --- 5. Pockej, az server nabehne (max 15 s) ---
Dim i
For i = 1 To 30
    WScript.Sleep 500
    If IsPortOpen(host, port) Then Exit For
Next

' --- 6. Otevri prohlizec ---
shell.Run "http://" & host & ":" & port, 1, False

' ============================================================
' Pomocne funkce
' ============================================================

' Precte hodnotu klice z config.ini (jednoducha implementace)
Function ReadIni(path, key, defaultValue)
    Dim stream, line, value, re
    ReadIni = defaultValue
    Set re = New RegExp
    re.IgnoreCase = True
    re.Pattern = "^\s*" & key & "\s*=\s*(.+?)\s*$"
    Set stream = fso.OpenTextFile(path, 1, False)
    Do While Not stream.AtEndOfStream
        line = stream.ReadLine
        ' Preskoc komentare
        If Left(Trim(line), 1) <> ";" And Trim(line) <> "" Then
            Dim matches
            Set matches = re.Execute(line)
            If matches.Count > 0 Then
                value = matches(0).SubMatches(0)
                If Len(value) > 0 Then ReadIni = value
                Exit Do
            End If
        End If
    Loop
    stream.Close
End Function

' Otestuje, jestli je port otevreny (server bezi)
Function IsPortOpen(h, p)
    Dim http, url
    IsPortOpen = False
    url = "http://" & h & ":" & p & "/"
    On Error Resume Next
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", url, False
    http.Send
    If Err.Number = 0 And http.Status > 0 Then IsPortOpen = True
    On Error Goto 0
End Function
