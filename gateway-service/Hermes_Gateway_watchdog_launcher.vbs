Set objShell = CreateObject("Wscript.Shell")
Set env = objShell.Environment("PROCESS")
env("VIRTUAL_ENV") = "C:\Users\thanhtam\AppData\Local\hermes\hermes-agent\venv"
env("PYTHONPATH") = "C:\Users\thanhtam\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages"
env("HERMES_HOME") = "C:\Users\thanhtam\AppData\Local\hermes"
env("PYTHONIOENCODING") = "utf-8"
env("HERMES_GATEWAY_DETACHED") = "1"
cmd = chr(34) & "C:\Users\thanhtam\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\pythonw.exe" & chr(34) & " " & chr(34) & "C:\Users\thanhtam\AppData\Local\hermes\gateway-service\Hermes_Gateway_launcher.py" & chr(34)
objShell.Run cmd, 0, False
