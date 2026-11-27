' Lancador da interface Tk nativa do Vilm (com ferramentas de producao)
Set sh = CreateObject("WScript.Shell")
sh.Environment("PROCESS")("PYTHONPATH") = "C:\Users\globaltec\AppData\Local\VilmPipeline\app"
sh.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
sh.Run """C:\Users\globaltec\AppData\Local\VilmPipeline\python\pythonw.exe"" -m lyrics_aligner.tkinter_app", 0, False
