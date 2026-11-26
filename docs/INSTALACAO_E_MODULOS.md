# Instalação dos módulos do pipeline

Os arquivos em `app/lyrics_aligner/` são **substituições/acréscimos drop-in** para uma
instalação do **Vilm Pipeline 1.3**. Eles não incluem runtime, modelos, mídia nem saídas
renderizadas — apenas o código-fonte.

## Como aplicar

1. Feche o Vilm Pipeline (ícone na bandeja → Sair).
2. Copie todo o conteúdo de `app/lyrics_aligner/` para:
   ```
   %LOCALAPPDATA%\VilmPipeline\app\lyrics_aligner\
   ```
   (substitua os arquivos de mesmo nome; os arquivos novos apenas sejam adicionados).
3. Copie `profiles.json` para:
   ```
   %LOCALAPPDATA%\VilmPipeline\profiles.json
   ```
4. Abra o Vilm Pipeline novamente.
5. Reinicie também o atalho da interface Tk se estiver em uso.

> Faça um backup da pasta `app\lyrics_aligner` antes de substituir.

## Dependências

Já estão incluídas no runtime do Vilm Pipeline 1.3: Python 3.12, FFmpeg/FFprobe,
Demucs, Whisper/stable-ts, librosa, soundfile, Pillow, numpy e scipy.

Para usar o CLI fora do aplicativo, defina:

```bat
set PYTHONPATH=C:\Users\globaltec\AppData\Local\VilmPipeline\app
```

## Arquivos

| Arquivo | Função |
|---|---|
| `karaoke.py` | Render ASS com `\kf` (preenchimento suave), rolagem com linha atual no topo, efeito de saída no fim do verso, estrofes (6 s) e limites de camada |
| `timing_core.py` | Regra proporcional: intervalo curto entre versos → 40% para o fim do anterior, 60% para o início do próximo |
| `cli.py` | Comandos `profiles`, `karaoke`, `make` e `audio` |
| `webapp.py` | API/UI de perfis no aplicativo web |
| `desktop.py` | Interface Tk: perfis, lote, QC, editor de cena (Canvas + timeline), snap, atalhos, autosave, histórico, áudio |
| `karaoke_jobs.py` | Pipeline central: alinhamento com cache, QC/preflight, ASS, fundo, burn, intro/logo e playback |
| `scene.py` | `scene.json`: camadas de imagem/texto, resolução, safe zone e compilação FFmpeg/ASS |
| `imagefx.py` | Remoção de fundo (cor/luma/saturação/flood) e ajustes de imagem com PIL/numpy/scipy |
| `audiofx.py` | Separação Demucs, mix de voz/instrumental, ruído e loudness EBU R128 |
| `profiles.json` | Perfis `festa-ibuc` e `festa-genesis` com configurações e extras |

## Comandos principais

```bat
:: listar perfis
python -m lyrics_aligner profiles

:: karaoke completo com perfil
python -m lyrics_aligner make cantado.mpeg letra.txt -o hino.mp4 --profile festa-ibuc

:: com cena visual e transição
python -m lyrics_aligner make cantado.mpeg letra.txt -o hino.mp4 --profile festa-ibuc --scene cena.json --transition fade

:: playback instrumental gerado pelo Demucs
python -m lyrics_aligner make cantado.mpeg letra.txt -o hino.mp4 --profile festa-ibuc --make-playback

:: áudio
python -m lyrics_aligner audio entrada.wav -o playback.wav --mode instrumental
python -m lyrics_aligner audio entrada.wav -o voz.wav --mode mix --voice-gain 1.6 --normalize
python -m lyrics_aligner audio entrada.wav -o limpo.wav --denoise 8 --enhance voz
```

Consulte `docs/LEIA-ME-Vilm.txt` para o manual completo e as regras de tempo.

## Atribuição

Derivado de fluxos do **Vilm Lyrics Aligner** (upstream MIT:
<https://github.com/banjuman/vilm-lyrics-aligner>). Atribuição preservada conforme o
README deste repositório.
