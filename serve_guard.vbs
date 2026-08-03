' Запускает сторож OzonFFHelper полностью скрыто (без чёрного окна).
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""D:\OzonFFHelper\serve_guard.ps1""", 0, False
