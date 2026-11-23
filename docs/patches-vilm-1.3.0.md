# Patches aplicados no runtime Vilm 1.3.0 (local)

Patches aplicados em instalação local do VilmPipeline (`%LOCALAPPDATA%\VilmPipeline`) durante manutenção do pipeline. Não são patches upstream oficiais.

## 1) `lyrics_aligner.publish.find_ffmpeg`
- **Problema:** FFmpeg empacotado em `%LOCALAPPDATA%\VilmPipeline\tools\ffmpeg.exe` não era encontrado.
- **Correção:** adicionar candidato `root / "VilmPipeline" / "tools" / "ffmpeg.exe"` na resolução.
- **Evidência:** `find_ffmpeg()` passou a retornar `C:\Users\globaltec\AppData\Local\VilmPipeline\tools\ffmpeg.exe`.

## 2) `lyrics_aligner.tk_pipeline_tab`
- **Problema:** UI não mostrava progresso durante alinhamento/burn.
- **Correção:** logs de etapa antes dos passos principais e `progress=self._log` em `align_to_srt`.
- **Limitação:** processo já em execução precisa reiniciar para carregar o código novo.

## 3) Variáveis de ambiente (usuário)
- `VILM_FFMPEG=%LOCALAPPDATA%\VilmPipeline\tools\ffmpeg.exe`
- `VILM_FFPROBE=%LOCALAPPDATA%\VilmPipeline\tools\ffprobe.exe`
- `%LOCALAPPDATA%\VilmPipeline\tools` adicionado ao `Path` do usuário.
