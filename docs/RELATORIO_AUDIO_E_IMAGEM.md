# Relatório técnico — Editor de áudio e tratamento de imagens

## ERRATA — 27/11/2026 (F4-I3)

> Documento histórico; o corpo abaixo foi preservado. As referências
> `arquivo:linha` e os rótulos "aba Karaokê", "seção Áudio/Cena" são do
> momento em que o relatório foi escrito. O estado atual está em
> `DESIGN_FUNCIONAL.md` e `INSTALACAO_E_MODULOS.md`.

1. **5 abas → 7 abas planas.** Áudio é a aba 5 e Cena é a aba 4, ambas lazy,
   dentro da mesma janela Tk; não são mais LabelFrames da aba Karaokê.
2. **63 chaves → 492 chaves com coreano.** `i18n.LANGUAGES` tem 492 chaves em
   `pt`, `en` e `ko`; a interface toda é retraduzida por um único funil.
3. **"11 módulos faltando" → presentes.** Inclusive `assistant_helpers` e
   `segment_realign`, citados no relatório de estrutura, existem em `app\`.
4. **"nenhum atalho" → existe atalho.** `Vilm Lyrics Aligner.lnk` em
   `C:\Users\globaltec\OneDrive\Desktop` → `wscript.exe` → `iniciar_tk.vbs`.
5. **"webapp em uso" → legado.** `webapp.py` e `VilmPipeline.exe` saíram do
   fluxo; remoção em paralelo (F4-I2), sem garantia nesta etapa.
6. **"texto não aparece na prévia" → prévia de Cena sim, Produção não.**
   `scene.render_preview` passou a compor as camadas de texto; a prévia da
   aba Produção permanece com frases fixas de exemplo.
7. **"autosave global" → por projeto.** `tk_scene_tab` grava em
   `<pasta>\trabalho\scene-autosave.json` quando o projeto tem
   `projeto.json`; o caminho global é só fallback sem projeto.
8. **"config.json sem dono" → `settings.json`.** `settings.py` é o dono das
   preferências; `config.json` e os demais arquivos antigos são fontes de
   migração única.

---

**Projeto:** Vilm Pipeline 1.3 — interface Windows (Tk)
**Data:** 27/11/2026
**Escopo:** ferramentas de áudio e de imagem aplicadas ao fluxo de produção de vídeos karaokê.

---

## 1. Visão geral

O editor de áudio e o tratamento de imagens não são módulos isolados: eles alimentam
o mesmo pipeline de produção usado pelo lote de hinos.

```
desktop.py (aba Karaokê)
   ├── seção Áudio  ──► audiofx.py ──► Demucs + FFmpeg
   ├── seção Cena   ──► scene.py   ──► imagefx.py ──► FFmpeg (overlay/ASS)
   └── lote         ──► cli.py make ──► karaoke_jobs.py
```

Arquivos principais:

| Arquivo | Responsabilidade |
|---|---|
| `app/lyrics_aligner/audiofx.py` | separação vocal/instrumental, ganhos, ruído, loudness, análise |
| `app/lyrics_aligner/imagefx.py` | remoção de fundo, transparência e ajustes de imagem |
| `app/lyrics_aligner/scene.py` | camadas, área segura, compilação da cena e prévia |
| `app/lyrics_aligner/desktop.py` | interface das seções Áudio e Cena |
| `app/lyrics_aligner/cli.py` | comandos `audio` e `make --scene` |

---

## 2. Editor de áudio

### 2.1 Onde aparece

Na aba **Karaokê** da interface Windows, seção
**“Áudio (voz, playback, ruído, qualidade)”**:

- campo de mídia com **Usar selecionada** (aproveita o item marcado no lote);
- botões **Analisar**, **Extrair instrumental**, **Voz +**, **Voz −**,
  **Reduzir ruído** e **Normalizar / melhorar**;
- caixa de log com a saída de cada processamento.

Por baixo, cada botão executa `python -m lyrics_aligner audio ...` em um
subprocesso com `PYTHONPATH` apontando para o app do Vilm.

### 2.2 Funções e parâmetros

| Função | O que faz | Tecnologia | Padrões |
|---|---|---|---|
| `separate_audio` | separa voz e instrumental | Demucs `htdemucs`, device `auto` | devolve `vocals` e `no_vocals.wav` |
| `mix_audio` | mistura voz + instrumental com ganhos independentes | FFmpeg `volume` + `amix` | `voice_gain=1.0`, `music_gain=1.0`, `normalize=0` |
| `denoise` | reduz ruído de banda larga | FFmpeg `afftdn` | `strength=10` (faixa 1–40), `nf=-40` |
| `normalize` | normalização de loudness EBU R128 | FFmpeg `loudnorm` | `I=-16 LUFS`, `TP=-1.5 dB`, `LRA=11` |
| `enhance` | clareza: corte de grave, EQ de presença, compressão e loudness | FFmpeg `highpass` + `equalizer` + `acompressor` + `loudnorm` | preset `voz` ou `musica` |
| `analyze` | duração, codec, taxa, canais, volume médio e pico | `ffprobe` + `volumedetect` | — |
| `apply_audio_fx` | aplica uma cadeia de efeitos | composição das funções acima | ordem fixa (abaixo) |

**Preset `voz`:** `highpass=75`, reforço em 3 kHz (+2,5 dB) e 8 kHz (+1,5 dB),
compressor `-18 dB / 3:1 / 20 ms / 150 ms`, `loudnorm -16`.

**Preset `musica`:** `highpass=40`, ajuste em 120 Hz e 8 kHz, compressor
`-16 dB / 2:1 / 30 ms / 200 ms`, `loudnorm -16`.

### 2.3 Ordem da cadeia em `apply_audio_fx`

```
1. denoise        (se strength > 0)
2. enhance        (se preset informado)
3. normalize      (se solicitado)
4. separate_audio (se instrumental ou ganhos diferentes de 1.0)
5. mix_audio      (ou usa somente o instrumental)
6. encode final   (44.1 kHz; PCM s16le, MP3 ou AAC conforme a extensão)
```

Observações técnicas:

- a separação acontece **depois** de ruído/melhoria/normalização;
- no modo `instrumental`, os ganhos de voz/música são ignorados (correto, pois só
  o instrumental é devolvido);
- `amix` usa `normalize=0` para que os ganhos configurados não sejam atenuados
  automaticamente;
- a saída da interface é sempre WAV; MP3/M4A/AAC só mudando a extensão de saída
  via CLI.

### 2.4 Ações da interface e arquivos gerados

| Botão | Comando interno | Arquivo gerado |
|---|---|---|
| Analisar | `audio --analyze` | nenhum (só métricas no log) |
| Extrair instrumental | `--mode instrumental` | `<mídia> - instrumental.wav` |
| Voz + | `--mode mix --voice-gain 1.6 --normalize` | `<mídia> - voz.wav` |
| Voz − | `--mode mix --voice-gain 0.6 --normalize` | `<mídia> - voz.wav` |
| Reduzir ruído | `--denoise 8 --normalize` | `<mídia> - limpo.wav` |
| Normalizar / melhorar | `--enhance voz --normalize` | `<mídia> - melhorado.wav` |

Os arquivos são gravados ao lado da mídia original; o trabalho intermediário do
Demucs fica em `trabalho/audio`.

### 2.5 Uso pela linha de comando

```powershell
$env:PYTHONPATH='C:\Users\globaltec\AppData\Local\VilmPipeline\app'
python -m lyrics_aligner audio "hino.mp4" -o "hino - instrumental.wav" --mode instrumental
python -m lyrics_aligner audio "hino.mp4" -o "hino - voz.wav" --mode mix --voice-gain 1.6 --normalize
python -m lyrics_aligner audio "hino.mp4" -o "hino - limpo.wav" --denoise 8 --normalize
python -m lyrics_aligner audio "hino.mp4" -o "hino - melhorado.wav" --enhance voz --normalize
python -m lyrics_aligner audio "hino.mp4" -o "x.wav" --analyze
```

### 2.6 Limitações atuais

1. **Interface bloqueia durante o processamento**: `_audio_run` usa
   `subprocess.run` de forma síncrona; não há barra de progresso nem cancelar.
2. **Sem pré-escuta (A/B)**: o resultado só é ouvido fora do app.
3. **Valores fixos na interface**: força de ruído 8, alvo -16 LUFS e preset
   `voz`; os demais valores só via CLI.
4. **Inconsistência de executável**: `_audio_run` troca `pythonw.exe` por
   `python.exe`, mas `_audio_analyze` usa `sys.executable` diretamente.
5. **Demucs é pesado**: não há escolha de dispositivo na seção de áudio nem
   cache de hastes separadas entre execuções.
6. **Sem limpeza automática** da pasta `trabalho/audio`.

---

## 3. Tratamento de imagens

### 3.1 Onde aparece

- Seção **“Composição de cena (imagens e texto)”** na aba Karaokê.
- Diálogo **Efeitos da imagem**, aberto por camada.
- `scene.json` / `cena.json`, que guarda o dicionário `fx` de cada camada.
- `scene.py`, que aplica o efeito antes de compor o vídeo.

### 3.2 Remoção de fundo

Métodos disponíveis em `remove_background`:

| Método | Como funciona | Melhor uso |
|---|---|---|
| `none` | mantém o alpha existente | PNG já transparente |
| `color` | remove pixels próximos da cor-chave | fundo de cor sólida |
| `luma` | remove pixels claros | fundo branco |
| `saturation` | remove pixels com baixa saturação | fundo cinza/neutro |
| `flood` | remove só a região de fundo conectada às bordas | fundo uniforme com objeto central |

Parâmetros:

- `key_color` — cor de referência (padrão `#FFFFFF`);
- `tolerance` — tolerância (0,02–0,9; padrão 0,12);
- `feather` — penumbra em pixels (padrão 1,5).

O alpha é calculado como máscara suave:

```
low  = tolerance * 0.55
high = tolerance
alpha = clip((distância - low) / (high - low), 0, 1) * 255
alpha = gaussiana(alpha, sigma=feather)
```

No método `flood`, o alpha é binário (0 no fundo conectado às bordas, 255 no
resto) e depois suavizado. O alpha resultante é **multiplicado** pelo alpha
original da imagem, preservando transparências já existentes.

**Importante:** não há *matting* por IA. O resultado é bom em fundos planos e
uniformes; cabelo, vidro, fumaça e fundos complexos ainda exigem recorte manual.

### 3.3 Ajustes e efeitos

Aplicados por `apply_adjustments`, nesta ordem:

| Etapa | Parâmetro | Faixa / padrão |
|---|---|---|
| Rotação | `rotate` | -180° a 180°, padrão 0 |
| Espelhamento | `flip_h`, `flip_v` | desligado |
| Brilho | `brightness` | 0–3, padrão 1 |
| Contraste | `contrast` | 0–3, padrão 1 |
| Saturação | `saturation` | 0–3, padrão 1 |
| Nitidez | `sharpness` | 0–3, padrão 1 |
| Preto e branco | `grayscale` | desligado |
| Sépia | `sepia` | desligado |
| Tingimento | `tint` + `tint_strength` | cor + 0–0,9 (padrão 0,2) |
| Desfoque | `blur` | 0–10 px |
| Opacidade | `opacity` | 0–1, padrão 1 |
| Sombra | `shadow`, `shadow_offset`, `shadow_blur` | deslocamento 8 px, desfoque 6 px |

A saída de `process_image` é sempre **PNG RGBA**, o que preserva a transparência
para a composição.

### 3.4 Integração com a cena

Cada camada de imagem no JSON aceita:

```json
{
  "type": "image",
  "file": "figura.png",
  "x": 120, "y": 80, "w": 600, "h": 400,
  "start": 4.0, "end": 12.0,
  "fade_in": 0.4, "fade_out": 0.4,
  "ken_burns": false,
  "fx": {
    "remove_background": "color",
    "key_color": "#FFFFFF",
    "tolerance": 0.12,
    "feather": 1.5,
    "brightness": 1.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "sharpness": 1.0,
    "blur": 0,
    "opacity": 1.0,
    "rotate": 0,
    "grayscale": false,
    "sepia": false,
    "flip_h": false,
    "flip_v": false,
    "shadow": false
  }
}
```

Na compilação (`compile_scene`):

1. a imagem passa por `process_image` e vira `fx_<n>.png` no diretório de trabalho;
2. é escalada com `force_original_aspect_ratio=decrease`, centralizada com `pad`
   transparente e convertida para `rgba`;
3. recebe `fade` de alpha na entrada/saída;
4. opcionalmente recebe `zoompan` (efeito Ken Burns);
5. é sobreposta com `overlay` e `enable='between(t,start,end)'`;
6. camadas de texto viram um ASS separado com `\pos` e `\fad` por camada.

A cena também define resolução e área segura:

| Preset | Resolução | Margem segura |
|---|---|---|
| `1920x1080` | 1920×1080 | 4% |
| `1280x720` | 1280×720 | 4% |
| `3840x2160` | 3840×2160 | 4% |
| `1080x1920` | 1080×1920 | 6% |
| `1080x1080` | 1080×1080 | 6% |

`validate_scene` avisa sobre fim anterior ao início, arquivo ausente, camada
fora da área segura e texto vazio.

### 3.5 Interface do editor de cena

- Canvas 480×270 com grid, **snap** configurável e retângulo vermelho tracejado
  da área segura.
- Camadas de imagem (azul) e texto (amarelo), com arrastar e redimensionar pela
  alça inferior direita.
- Lista de camadas e campos de propriedade: X, Y, largura, altura, entra, sai,
  fade in, fade out, texto, fonte e cor.
- Timeline com barras por camada; arrastar move, arrastar na extremidade
  redimensiona.
- Botões: nova cena, abrir/salvar JSON, adicionar imagem/texto, remover,
  desfazer/refazer, prévia.
- **Autosave** em `%LOCALAPPDATA%\LyricsAligner\scene-autosave.json`,
  restaurado ao abrir.
- Diálogo de efeitos com método de remoção, cor, tolerância, penumbra, brilho,
  contraste, saturação, nitidez, desfoque, opacidade, rotação, P&B, sépia,
  espelhar, inverter e sombra.

### 3.6 Limitações atuais

1. **Texto não aparece na prévia**: `render_preview` processa apenas camadas de
   imagem; o texto só existe no vídeo final.
2. **Canvas fixo em 480×270**: para `1080x1920` e `1080x1080` a prévia fica
   distorcida, embora as coordenadas reais estejam corretas.
3. **Sombra pode cortar nas bordas**: o canvas da sombra tem o mesmo tamanho da
   imagem, sem expansão.
4. **`flood` depende do fundo tocando as bordas**; objetos encostados na borda
   podem ser removidos junto.
5. **Sem cache de imagem processada**: cada prévia e cada renderização refaz o
   efeito.
6. **Controles não expostos**: `tint`, `tint_strength`, `shadow_offset`,
   `shadow_blur` e `ken_burns` funcionam no JSON/compilador, mas não têm campo
   no diálogo.
7. **Sem correção de orientação EXIF** ao abrir a imagem.

---

## 4. Como as duas ferramentas entram na produção

### 4.1 Áudio → playback do karaokê

1. A mídia cantada é analisada.
2. **Extrair instrumental** gera o playback.
3. O arquivo é renomeado/posicionado na pasta do hino com `PLAYBACK` no nome.
4. O lote detecta `PLAYBACK*` e passa `--playback` para o `make`.
5. O vídeo final usa o playback como base e a legenda sincronizada.

### 4.2 Imagem → cena do vídeo

1. A cena é montada no canvas e salva em JSON.
2. O lote salva a cena em `trabalho/cli/scene.json` e passa `--scene`.
3. `karaoke_jobs.py` chama `compile_scene` ao final da geração.
4. As imagens são processadas por `imagefx.py` e compostas por FFmpeg.
5. O QC registra avisos de cena (arquivo ausente, fora da área segura etc.).

---

## 5. Validação realizada

- `python -m py_compile` e `compileall` do pacote: **OK**.
- Aplicação Tk inicia com **5 abas** e a aba Karaokê embutida: **OK**.
- Idioma padrão **pt-BR** e seletor de idioma: **OK**.
- Geração de vídeos com intro, logo, playback e cena: já exercitada nos 5 hinos
  IBUC (antes da unificação da interface).
- **Não há suíte de testes automatizados** para áudio/imagem; a validação é
  manual, com arquivos reais.

---

## 6. Recomendações priorizadas

| Prioridade | Ação |
|---|---|
| Alta | Rodar áudio em thread com progresso e cancelar (hoje trava a janela) |
| Alta | Renderizar camadas de texto na prévia da cena |
| Alta | Unificar a escolha de `python.exe`/`pythonw.exe` nas ações de áudio |
| Média | Adaptar o canvas ao formato vertical/quadrado, sem distorção |
| Média | Expor `tint`, sombra e Ken Burns no diálogo de efeitos |
| Média | Cachear imagens processadas por hash de arquivo + `fx` |
| Baixa | Presets de áudio na interface (voz, música, instrumental) |
| Baixa | Limpeza automática de `trabalho/audio` e `trabalho/cli` |
| Baixa | Corrigir orientação EXIF e perfil de cor ao abrir imagens |

---

## 7. Referência rápida

```powershell
# Compilar e validar o pacote
$env:PYTHONPATH='C:\Users\globaltec\AppData\Local\VilmPipeline\app'
& 'C:\Users\globaltec\AppData\Local\VilmPipeline\python\python.exe' -m compileall -q 'C:\Users\globaltec\AppData\Local\VilmPipeline\app\lyrics_aligner'

# Áudio
python -m lyrics_aligner audio "hino.mp4" -o "hino - instrumental.wav" --mode instrumental

# Vídeo com cena
python -m lyrics_aligner make "hino.mp4" "letra.txt" -o "saida\hino.mp4" `
  --profile festa-ibuc --scene "cena.json" --transition fade
```

**Situação no Git:** as ferramentas de áudio/imagem fazem parte do commit
`7c26394` (`audiofx.py`, `imagefx.py`, `scene.py`). As mudanças posteriores da
interface única (aba embutida, PT-BR padrão e este relatório) ainda não foram
commitadas.
