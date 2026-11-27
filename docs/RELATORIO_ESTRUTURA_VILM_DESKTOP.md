# Relatório — Estrutura do **Vilm Desktop (Tk)** × funcionalidades declaradas

## ERRATA — 27/11/2026 (F4-I3)

> Este é um documento histórico. O corpo abaixo foi escrito antes da unificação
> da interface e **não foi apagado**; as referências `arquivo:linha` nele são
> carimbo da época e **não devem ser usadas como prova do estado atual**.
> O estado atual está em `DESIGN_FUNCIONAL.md`, `INSTALACAO_E_MODULOS.md`,
> `LEIA-ME.txt` e na seção "ESTADO FINAL 27/11/2026" da
> `ESTRATEGIA_DESIGN_VILM_DESKTOP.md`.

1. **5 abas → 7 abas planas.** A casca atual (`tkinter_app.py ->
   VilmTkApp`) monta, nesta ordem: Projetos, Produção, Legendas (rótulo
   "Editor de cues"), Cena, Áudio, Configurações e Assistente. Produção,
   Cena e Áudio são lazy; as outras quatro são eager. Não há mais aba
   "Karaokê" com LabelFrames empilhados.
2. **63 chaves → 492 chaves, com coreano.** `i18n.LANGUAGES` tem 492 chaves
   em cada um de `pt`, `en` e `ko`, com o mesmo conjunto de chaves. A troca
   usa um único funil (`VilmTkApp.apply_language` → `i18n.retranslate`).
   O texto privado `TEXT` e os 37 rótulos fixos não são mais o mecanismo
   de idioma do produto.
3. **"11 módulos faltando" → presentes.** `assistant_helpers`,
   `segment_realign`, `burn_lock`, `staging`, `export_formats`, `metrics`,
   `inspect_ass`, `compare_versions`, `cross_check`, `diff_timelines` e
   `verify_frames` existem em `app\lyrics_aligner\`.
4. **"nenhum atalho" → existe atalho.** Há `Vilm Lyrics Aligner.lnk` em
   `C:\Users\globaltec\OneDrive\Desktop`, apontando para `wscript.exe` com
   `iniciar_tk.vbs`. O grupo "Vilm Pipeline" no Menu Iniciar é histórico.
5. **"webapp em uso" → legado.** O Flask (`webapp.py`) e `VilmPipeline.exe`
   estão fora do fluxo atual; a remoção está sendo feita em paralelo
   (F4-I2), sem garantia de conclusão nesta etapa.
6. **"texto não aparece na prévia" → prévia de Cena sim, Produção não.**
   `scene.render_preview` compõe camadas de texto (via ASS) além de imagens;
   a prévia da aba Produção ainda usa frases fixas de exemplo.
7. **"autosave global" → por projeto.** `tk_scene_tab._scene_autosave_path`
   usa `<pasta>\trabalho\scene-autosave.json` quando há `projeto.json`;
   o arquivo global em `%LOCALAPPDATA%\LyricsAligner\scene-autosave.json`
   é apenas fallback legado, sem projeto associado.
8. **"config.json sem dono" → `settings.json`.** O dono das preferências é
   `settings.py`, cujo destino canônico é `%LOCALAPPDATA%\LyricsAligner\
   settings.json`; os arquivos antigos são migrados uma vez.

---

> Análise feita sobre os arquivos reais em `app\lyrics_aligner\` da instalação
> `C:\Users\globaltec\AppData\Local\VilmPipeline`.
> Referências no formato `arquivo:linha` apontam para o código-fonte verificado.

---

## 1. Mapa real da aplicação

```
VilmPipeline\
├── iniciar_tk.vbs                 ← único lançador da interface Tk
├── profiles.json                  ← perfis karaokê (única fonte de perfis)
├── LEIA-ME.txt                    ← STILL descreve a interface WEB (bandeja/navegador)
├── docs\DESIGN_FUNCIONAL.md       ← único doc novo (os outros 2 prometidos não existem)
├── app\lyrics_aligner\             ← 55 módulos  ★ versão "nova"
└── python\Lib\site-packages\lyrics_aligner\  ← 50 módulos ★ versão ANTIGA (parcial)
```

### Janela principal (`tkinter_app.py`) — 5 abas

| Aba | Arquivo | Conteúdo real |
|---|---|---|
| Pipeline | `tk_pipeline_tab.py` (31 KB) | mídia cantada/playback, letra, alinhar, queimar MP4, staging, exportações, **batch próprio** |
| Configurações | `tk_settings_tab.py` (18 KB) | formulário de estilo/alinhamento (base `DEFAULT_SETTINGS` vinda do **webapp**) — **não tem seletor de perfil e nada é salvo** |
| Legendas | `tk_cue_editor.py` (18 KB) | editor de cues, undo/redo, realign, autosave |
| Assistente | `tk_assistant_tab.py` (6 KB) | refrões, ortografia, tradução (via `opencode_bridge`) |
| **Karaokê** | `desktop.py` (69 KB, 1474 linhas, **1 classe com ~90 métodos**) | ver abaixo |

### Aba Karaokê (`desktop.py`) — 4 blocos empilhados (não são sub-abas)

```
DesktopApp._build()  (desktop.py:300-559)
├── Cabeçalho: idioma PT/EN/KO + perfil
├── 1. Mídia + 2. Faixa na onda + 3. Letra + 4. Estilo → "Gerar SRT"
├── LabelFrame "Vídeo karaokê (lote)"   (desktop.py:424)  → QC, histórico, nomenclatura
├── LabelFrame "Áudio"                  (desktop.py:469)  → analisar/demucs/voz/ruído
└── LabelFrame "Composição de cena"     (desktop.py:486)  → canvas, timeline, props, efeitos
```

> **O `DESIGN_FUNCIONAL.md` mostra Produção/Cena/Áudio como três sub-abas do
> "Karaokê".** Na verdade são três `LabelFrame` empilhados verticalmente dentro de
> **um único `ttk.Frame` sem barra de rolagem** (`desktop.py:301`). Com a janela
> padrão de 1100×720 o conteúdo não cabe e é **cortado** — não há como alcançar a
> timeline da cena sem redimensionar a janela manualmente.

---

## 2. Quadro comparativo: o que foi declarado × o que existe

Legenda: ✅ existe e funciona · ⚠️ existe parcialmente / com ressalva · ❌ não existe ou está quebrado

### 2.1 Motor de karaokê e tempos

| Item | Status | Evidência |
|---|---|---|
| `\kf` preenchimento suave | ✅ | `karaoke.py` |
| lead 600 / end_pad 500 / regra 40-60 | ✅ | `profiles.json`, `timing_core.py` |
| Agrupamento ≤6 s e modo por estrofes | ✅ | CLI `karaoke --stanza-mode` |
| Letra preta, sem contorno/sombra | ✅ | perfil `festa-ibuc` (`outline: 0`, `shadow: 0`) |
| **Aplicado automaticamente na aba Karaokê** | ⚠️ | `_apply_profile()` (`desktop.py:561`) só copia `max_chars`, `max_duration`, `end_pad`, `lead_ms` e `mode`. **Não aplica fonte, cores, margens, transição, stanza_mode, fundo nem resolução.** |
| **Aplicado na aba Configurações** | ❌ | `tk_settings_tab` não tem campo de perfil (busca por `profile\|perfil` = 0 ocorrências). O LEIA-ME afirma que existe. |

### 2.2 Perfis

| Item | Status | Evidência |
|---|---|---|
| `festa-ibuc` / `festa-genesis` em `profiles.json` | ✅ | verificado, CLI `profiles` lista os dois |
| `load_profiles`/`save_profiles` unificados | ✅ | `karaoke_jobs.py:48,58` |
| **"Aplicados automaticamente a vídeos novos"** | ⚠️ | Só no lote (`--profile` em `desktop.py:756`). O "Gerar SRT" da mesma aba **não passa `--profile`** (`desktop.py:1346-1355`). |
| **`save_profiles` chamado pela interface** | ❌ | nenhum chamador na UI — não é possível criar/editar um perfil pela interface |
| **`projects_root` do perfil usado** | ⚠️ | usado só para localizar o logo (`desktop.py:580`). **Não há seletor de pasta de projetos** no desktop, embora o doc afirme. |

### 2.3 Produção e qualidade

| Item | Status | Evidência |
|---|---|---|
| `karaoke_jobs.run_project` + QC + preflight | ✅ | `karaoke_jobs.py:115,297` |
| Lote chamando `make` com `--profile/--scene/--transition` | ✅ | `desktop.py:755-768` |
| QC exibido na tela lendo `qc.json` | ✅ | `desktop.py:777-782, 704` |
| Histórico `history.csv` | ✅ | `desktop.py:683` |
| Nomenclatura `{pasta}` | ✅ | `desktop.py:675` |
| Intro 4 s + logo IBUC | ✅ | `karaoke_jobs._build_final` (`karaoke_jobs.py:222`) |
| **Arquivos de saída com nome do hino** | ⚠️ | só no lote. O "Gerar SRT" salva em `Documents\Vilm Lyrics Aligner\<mídia>-<timestamp>.srt` (`desktop.py:1297`) |

### 2.4 Cena e imagem

| Item | Status | Evidência |
|---|---|---|
| `scene.py` com camadas, timeline, presets, área segura, xfade | ✅ | `scene.py` |
| `imagefx.py` com remoção de fundo e ajustes | ✅ | `imagefx.py` |
| Canvas, snap, grade, área segura, timeline arrastável | ✅ | `desktop.py:818-991` |
| Undo/redo, autosave, atalhos | ✅ | `desktop.py:789-817, 211-217` |
| Prévia real (`render_preview`) | ✅ | `desktop.py:1123` |
| Diálogo de efeitos | ✅ | `desktop.py:1062` (mas **só aceita camada de imagem** — não valida o tipo, avisa "selecione uma camada de imagem" mesmo com texto selecionado) |
| **Grade correta em outros formatos** | ❌ | `desktop.py:821` usa `scale = 480/1920` fixo para a grade, mas `scale_x = 480/sw` para as camadas → em `1080x1920`/`1080x1080` a grade não acompanha |
| **Barra de rolagem da aba** | ❌ | não existe (ver §1) |

### 2.5 Áudio

| Item | Status | Evidência |
|---|---|---|
| `audiofx.py` com Demucs, ganhos, ruído, EBU R128 | ✅ | `audiofx.py`, CLI `audio` |
| Botões na aba Karaokê | ✅ | `desktop.py:476-482` |
| **Execução sem travar a interface** | ❌ | `_audio_run`/`_audio_analyze` usam `subprocess.run` **síncrono na thread da UI** (`desktop.py:254,269`). O lote sim usa thread (`desktop.py:721`). |

### 2.6 CLI

| Item | Status |
|---|---|
| `profiles`, `karaoke`, `make`, `audio` | ✅ todos implementados e testados |

### 2.7 Interface Windows única

| Item | Status | Evidência |
|---|---|---|
| 5 abas, aba Karaokê embutida, sem janela filha | ✅ | `tkinter_app.py:117-133` |
| **Webapp "revertido / fora do fluxo"** | ❌ | `webapp.py` (159 KB) continua: menu "Abrir interface web" (`tkinter_app.py:55,89`), `tk_settings_tab.py:15` importa `DEFAULT_SETTINGS` dele, `tk_pipeline_tab.py:189` importa `probe_media`. Ele foi **absorvido como biblioteca**, não removido. |
| **PT-BR como padrão do programa** | ⚠️ | `desktop.py:171` inicia em `pt`. Mas: (a) só **42 chaves** do `TEXT` são aplicadas (`_apply_language`, `desktop.py:1144`) e há **37 rótulos em português fixo** no lote/cena/áudio que nunca são traduzidos; (b) as outras 4 abas usam **outro sistema** (`i18n.t`, 128 chaves) trocado só pela janela "Configurações avançadas" (`tk_config_window.py:32`). **O seletor de idioma do cabeçalho não muda as outras abas.** |
| **Módulos copiados** | ⚠️ | `timeline_validation`, `opencode_bridge`, `project`, `resolve_bridge` estão em `app\`. **Faltam 11** (ver §4.1). |
| **Atalho da área de trabalho** | ❌ | nenhum `.lnk`/`.vbs` em `Desktop` (usuário), `C:\Users\Public\Desktop` ou Menu Iniciar. Só existe `iniciar_tk.vbs` na raiz da instalação. |
| **`docs/INSTALACAO_E_MODULOS.md`** | ❌ | não existe |
| **`docs/LEIA-ME-Vilm.txt`** | ❌ | não existe |
| `LEIA-ME.txt` atualizado | ❌ | continua descrevendo a **interface web** ("clique no atalho Vilm Pipeline, abre no navegador, ícone da bandeja") e um preset **contraditório** (lead 400 ms, 3 linhas por tela, letra branca com contorno preto) |

---

## 3. Os conceitos que nunca foram definidos

Esta é a raiz do "não está nada funcional". Existem **3 modelos paralelos competindo**:

### 3.1 PROJETO — 3 definições incompatíveis

| Modelo | Onde | Como é identificado | Onde guarda dados |
|---|---|---|---|
| **A. `HymnProject`** (cues + histórico de alterações) | `project.py:93` | um `.json` exportado à mão | `tk_pipeline_tab.py:545,565` — só "Exportar/Importar projeto". **Nunca é salvo automaticamente.** |
| **B. Pasta-convenção** | `desktop.py:737-752` | pasta contendo `letra.txt`, `FUNDO*`, `PLAYBACK*`, imagem de título e `saida\` | `history.csv`, `qc.json`, `trabalho\` dentro da pasta |
| **C. `projects_root` do perfil** | `profiles.json` / `webapp-config.json` | raiz onde as pastas dos hinos ficam | usado só pelo webapp e para achar o logo |

A aba **Pipeline** tem um "projeto" (A), o **lote Karaokê** tem outro (B), o **webapp** tem outro (C).
O usuário não tem como saber qual é o projeto do programa.

### 3.2 PERFIL — o que é de fato

> **Definição implícita no código:** um *preset de configuração nomeado*, guardado em
> `profiles.json`, com `settings` (alignment/karaoke/background/output/burn),
> `extras` (intro/logo/regras) e `projects_root`.

Problemas:
1. **Só é aplicado em 2 dos 4 caminhos de geração** (lote `make` ✅ · "Gerar SRT" ❌ · Pipeline `align` ❌ · Pipeline `burn` ❌).
2. **Não é editável pela interface** (`save_profiles` nunca é chamado pela UI).
3. **Não tem descrição de "para que serve" visível**: o texto exibido é o campo `description` livre do JSON (`desktop.py:576`) — nada diz ao usuário *o que muda* ao trocar.
4. **Não existe na aba Configurações**, onde a documentação diz que ele está.
5. É confundido com **"predefinições"** (o `SANTO_PRESET` do webapp, `tk_settings_tab.py:287`) — outro preset com o mesmo propósito e nome parecido.

### 3.3 CONFIGURAÇÃO — 4 arquivos em 4 lugares

| Arquivo | Caminho | Escrito por | Lido por |
|---|---|---|---|
| `desktop-preferences.json` | `%LOCALAPPDATA%\LyricsAligner\` | `desktop.py:636,1177` | `desktop.py:171,188` |
| `config.json` | `%LOCALAPPDATA%\LyricsAligner\` | **ninguém** ❌ | `desktop.py:174` |
| `.vilm_tkinter_config.json` | `%USERPROFILE%\` | `tk_advanced_settings.py:38` | `tk_advanced_settings.py:31` |
| `webapp-config.json` | `%LOCALAPPDATA%\VilmPipeline\` | webapp | webapp |

Mais a aba **Configurações**, que é um formulário **sem botão de salvar** —
`collect()` só é chamado no momento da geração (`tk_pipeline_tab.py:107,155`).
Ou seja: o usuário ajusta fonte, cor, margens, resolução… e **tudo é descartado ao
fechar o programa**.

---

## 4. Falhas estruturais verificadas

### 4.1 `app\` está com **11 módulos faltando** → botões quebrados

| Módulo ausente | Chamado em | O que quebra |
|---|---|---|
| `assistant_helpers` | `tk_assistant_tab.py:79,90,109` | **Aba Assistente inteira** (refrões, ortografia, tradução) |
| `segment_realign` | `tk_cue_editor.py:285` | "Realinhando seleção" na aba Legendas |
| `burn_lock` | `tk_pipeline_tab.py:173` | **"Gerar MP4 karaoke"** da aba Pipeline |
| `staging` | `tk_pipeline_tab.py:485` | Botão "Preparar enviar_agora" |
| `export_formats` | `tk_pipeline_tab.py:535` | "Exportar multi-formato" |
| `metrics`, `inspect_ass`, `compare_versions`, `cross_check`, `diff_timelines`, `verify_frames` | scripts de verificação | inspeção/validação |

Esses arquivos existem **apenas** em `python\Lib\site-packages\lyrics_aligner\`.
Como os imports são *lazy* (dentro das funções), **o programa abre normalmente e só
explode quando o botão é clicado**.

### 4.2 Existem **duas versões diferentes do programa** na mesma instalação

```
app\lyrics_aligner\                 → tem karaoke_jobs, scene, imagefx, audiofx   (NOVO)
site-packages\lyrics_aligner\       → tem burn_lock, staging, export_formats, ...  (ANTIGO)
```

* `iniciar_tk.vbs` define `PYTHONPATH=app` → roda a versão **nova**.
* Rodar `python -m lyrics_aligner.tkinter_app` **sem** `PYTHONPATH` →
  carrega `site-packages` → **desktop.py antigo (25 KB), sem perfis, sem cena, sem QC**,
  e `tkinter_app` antigo **sem a aba Karaokê**.

Verificado na prática: `lyrics_aligner.__file__` muda conforme a variável de ambiente.

### 4.3 Quatro fluxos de geração paralelos, com saídas diferentes

| Fluxo | Comando | Onde grava |
|---|---|---|
| Aba Pipeline → "Alinhar" | `align_to_srt` em processo | `<mídia>_lyrics\lyrics.srt` (`tk_pipeline_tab.py:113`) |
| Aba Pipeline → "Batch (pasta)" | `align_to_srt` | `<áudio>_saida\lyrics.srt` (`tk_pipeline_tab.py:633`) |
| Aba Karaokê → "Gerar SRT" | subprocess CLI `align` | `Documents\Vilm Lyrics Aligner\*.srt` (`desktop.py:1297`) |
| Aba Karaokê → "Gerar vídeos" | subprocess CLI `make` | `<pasta>\saida\<hino>.mp4` (`desktop.py:750`) |

Nenhum alimenta o outro: o SRT gerado em um fluxo **não aparece** no editor de cues do outro.

### 4.4 Bugs pontuais confirmados

1. **Trocar o idioma apaga o perfil salvo.**
   `_language_changed` grava `{"ui_language": ...}` por cima do arquivo inteiro
   (`desktop.py:1177`), sem mesclar. `_profile_changed` (`desktop.py:636`) grava
   `{**self.preferences, ...}` com o dicionário carregado **no `__init__`**, ou seja,
   já defasado. Sequência "trocar perfil → trocar idioma" = perfil perdido.
2. **Seletor de dispositivo morto.** `self.backend` vem de `config.json`
   (`desktop.py:174`), arquivo que **não existe e nunca é escrito** por nenhum módulo
   → sempre `"cpu"` → o combobox só oferece **"CPU"** (`desktop.py:380-386`).
   CUDA/Metal nunca aparecem.
3. **`webapp` não foi removido** — ver §2.7.
4. **Áudio congela a UI** (`desktop.py:254,269`).
5. **Grade desalinhada** fora de 1920×1080 (`desktop.py:821`).
6. **Dois sistemas de idioma independentes** que não conversam (§2.7).
7. **Lixeira no pacote:** `karaoke.py.bak-20261119` (39 KB) junto dos módulos.
8. **Nenhum atalho** na área de trabalho/menu iniciar (§2.7).

---

## 5. Diagnóstico — por que parece "uma bagunça"

1. **Não há núcleo de domínio.** Não existe um objeto `Project`/`Job` que carregue
   mídia + letra + perfil + cena + saída. Cada aba inventa o seu próprio e os dados
   não viajam entre elas.
2. **O `desktop.py` é um "script monolítico de 1474 linhas" com 90 métodos** misturando
   4 domínios distintos (alinhamento, lote, áudio, cena). É a única parte da UI que
   **não** segue o padrão "aba = 1 arquivo pequeno" das outras 4.
3. **Configuração sem dono:** 4 arquivos, 1 formulário sem salvar, 1 arquivo lido mas
   nunca escrito, 2 presets concorrentes (`profiles.json` × `SANTO_PRESET`).
4. **Documentação desatualizada e contraditória:** o `LEIA-ME.txt` ainda é o manual da
   versão web; o `DESIGN_FUNCIONAL.md` descreve uma hierarquia de sub-abas que não
   existe; dois docs prometidos não foram criados.
5. **A instalação tem duas cópias do código** e o resultado depende de `PYTHONPATH`.

---

## 6. Definições propostas (para alinhar código e entendimento do usuário)

> **PROJETO** = um hino. Uma pasta com `letra.txt`, mídia, `FUNDO*`, `PLAYBACK*`,
> imagem de título, e as saídas em `saida\`. Guarda `projeto.json` (versão 1) com
> caminho da mídia, playback, perfil usado, faixa processada, referência da cena e
> histórico. *Uma tela "Projetos" lista as pastas e abre/carrega esse JSON.*
> → hoje o programa já tem a **pasta** (modelo B) e o `HymnProject` (modelo A), mas
> estão desconectados: `project.py` deve passar a ser a serialização do projeto de pasta.

> **PERFIL** = preset de *estilo + tempo + acabamento* para um evento
> (`festa-ibuc`, `festa-genesis`). Não contém caminhos de mídia. Aplicado a **todos**
> os caminhos de geração, com botão "Salvar ajustes neste perfil" e um tooltip que
> lista o que ele muda. `projects_root` sai do perfil e vira propriedade do
> *local de trabalho* (Configurações).

> **CONFIGURAÇÃO** = máquina + preferências globais (idioma, tema, dispositivo,
> pasta de projetos, nomenclatura). **Um único arquivo** `%LOCALAPPDATA%\LyricsAligner\config.json`,
> escrito por um único módulo `settings.py`, lido por todos.

---

## 7. Plano de reorganização sugerido

**Fase 1 — fazer funcionar (1 sessão)**
1. Copiar para `app\` os 11 módulos que faltam (`assistant_helpers`, `burn_lock`,
   `staging`, `export_formats`, `segment_realign`, `metrics`, `inspect_ass`,
   `compare_versions`, `cross_check`, `diff_timelines`, `verify_frames`).
2. Eliminar a cópia divergente de `site-packages` (ou torná-la um *stub* que só
   aponta `PYTHONPATH` para `app\`), para que o programa seja sempre o mesmo.
3. Corrigir `desktop.py:1177` (mesclar preferências em vez de sobrescrever).
4. Remover `karaoke.py.bak-20261119`.

**Fase 2 — eliminar duplicações**
5. Um único fluxo de geração: aba Pipeline passa a chamar `karaoke_jobs.run_project`
   (mesmo caminho do lote); "Gerar SRT" do `desktop.py` vira a versão curta do mesmo
   serviço, **recebendo `--profile`**.
6. Um único sistema de idioma: `desktop.py` deixa de ter `TEXT` próprio e passa a
   usar `i18n.t()`; o seletor do cabeçalho chama `i18n.set_language()` e refaz o
   texto de **todas** as abas.
7. Um único arquivo de configuração (item 3 da §6) + abas de configuração com
   "Salvar".

**Fase 3 — estrutura e clareza**
8. Dividir `desktop.py` em 4 módulos de aba (`tk_production_tab.py`,
   `tk_batch_tab.py`, `tk_audio_tab.py`, `tk_scene_tab.py`), deixando `desktop.py`
   só como casca/compatibilidade com `embedded=True`.
9. Transformar os 4 blocos em um `ttk.Notebook` interno **ou** colocar `outer` dentro
   de um Canvas com scrollbar (hoje o conteúdo é cortado).
10. Tela de **Projetos** ligando `project.py` às pastas; botão **"Novo perfil"** e
    **"Salvar neste perfil"** usando `save_profiles`.
11. Reescrever `LEIA-ME.txt` para a interface Tk e criar
    `INSTALACAO_E_MODULOS.md` / `LEIA-ME-Vilm.txt` (hoje inexistentes).
12. Recriar o atalho da área de trabalho e commitar/pushar tudo o que está só local.

---
---

# PARTE II — Varredura profunda, áudio e imagem

> Complemento do relatório acima. Inclui (A) novos achados estruturais verificados no
> código e nas logstes de execução, (B) a integração do relatório
> `RELATORIO_AUDIO_E_IMAGEM.md` na estrutura geral, e (C) as correções que este
> capítulo traz sobre a Parte I.

---

## A. Novos achados estruturais

### A.1 O webapp **continua vivo e é ele que está em uso real**

`logs\webapp.log` (2,2 MB) mostra sessões recentes:

```
2026-11-26 18:41:46  Servidor iniciado. PID 28548
2026-11-26 18:41:47  Vilm Lyrics Aligner (web): http://127.0.0.1:8765/pipeline
2026-11-26 18:41:47  Pasta de projetos: C:\Users\globaltec\Downloads\festa do genesis
2026-11-26 18:42:25  "GET /api/profiles HTTP/1.1" 200
```

* `webapp.py:2089` → `app.run(host, port, threaded=True, debug=False)` (**servidor de
  desenvolvimento Flask** usado como produto).
* As rotas usadas hoje são justamente **`/api/profiles`** e **`/api/projects`** —
  ou seja, a seleção de **perfil** e a noção de **projeto** que *funcionam* no
  dia a dia estão no webapp, não na aba Tk.
* O log acumula **1.495 linhas de erro**, incluindo `MemoryError` recorrente (17/11).

> **Conclusão:** o webapp não foi "revertido para fora do fluxo". Ele é a interface
> que o usuário realmente abriu nos últimos dias, e continua sendo a única com
> lista de projetos + perfis integrados.

### A.2 Três executáveis instalados, apontando para a cópia antiga

`python\Scripts\` (gerados pelo pacote `lyrics_aligner-1.3.0`, 22/11):

| Executável | Entry point | Pacote usado |
|---|---|---|
| `lyrics-aligner.exe` | `lyrics_aligner.cli:main` | `site-packages` |
| `vilm-lyrics-aligner.exe` (**gui**) | `lyrics_aligner.desktop:main` | `site-packages` ← **copia antiga, sem perfis/cena/QC** |
| `vilm-webapp.exe` | `lyrics_aligner.webapp:main` | `site-packages` |

Como `site-packages\lyrics_aligner\` **não contém** `karaoke_jobs.py`, `scene.py`,
`imagefx.py` nem `audiofx.py`, qualquer atalho que aponte para
`vilm-lyrics-aligner.exe` **abre o programa antigo, sem a aba Karaokê**.

Resumo dos três caminhos de lançamento possíveis hoje:

```
iniciar_tk.vbs                → PYTHONPATH=app        → programa NOVO (com perfis/cena)
vilm-lyrics-aligner.exe       → sem PYTHONPATH         → programa ANTIGO
python -m lyrics_aligner...   → conforme ambiente      → programa imprevisível
```

### A.3 Código morto (nunca importado por ninguém)

| Módulo | Tamanho | Conteúdo |
|---|---|---|
| `runtime_setup.py` | 2,5 KB | setup de execução — não chamado |
| `resolve_bridge.py` | 14,5 KB | ponte com DaVinci Resolve — **não exposta em nenhuma tela** |
| `cleanup.py` | 1,7 KB | limpeza — não chamado |

### A.4 Zero testes automatizados

Nenhum `test_*.py` nem pasta de testes em `app\`. A única validação registrada foi
`compileall` (sintaxe) e conferência manual com arquivos reais.

### A.5 i18n — três sistemas, um deles sem coreano

Verificado em tempo de execução:

```python
LANGUAGES = {'pt': 63 chaves, 'en': 63 chaves}
available_languages() == ['pt', 'en']      # ← sem 'ko'
```

Mas a interface oferece **três** seletores:

| Onde | Opções | O que muda |
|---|---|---|
| Cabeçalho da aba Karaokê (`desktop.py:308`) | Português / English / **한국어** | só o `TEXT` próprio (42 chaves) daquela aba |
| Janela "Configurações avançadas" (`tk_config_window.py:8`) | `available_languages()` → **pt, en** | as outras 4 abas via `i18n.t()` |
| — | — | **nunca há coreano nas abas Pipeline/Config/Legendas/Assistente** |

> **Correção à Parte I:** o `i18n.py` tem **63 chaves por idioma** (128 linhas no
> arquivo contam as duas versões). Não 128 chaves.

### A.6 Higiene da instalação

* `karaoke.py.bak-20261119` solto em `app\lyrics_aligner\`.
* `__pycache__\` completo distribuído junto dos fontes.
* `models\` 576 MB · `python\Lib` 1,07 GB · `tools\ffmpeg+ffprobe` 445 MB · `app\` 1 MB.
* `tools\` só contém `ffmpeg.exe` e `ffprobe.exe` — nada de utilitários próprios.

---

## B. Onde áudio e imagem se encaixam na estrutura

### B.1 Editor de áudio

```
aba Karaokê → LabelFrame "Áudio" (desktop.py:469-484)
   métodos _audio_* (desktop.py:220-289)
        └── subprocess python -m lyrics_aligner audio …
                 └── cli.py (parser "audio")
                        └── audiofx.py  ← FFmpeg/Demucs
```

| Recurso | Técnica | Botão na UI |
|---|---|---|
| Separar voz/instrumental | Demucs `htdemucs` (`audiofx.py:23`) | Extrair instrumental |
| Ganho voz/música | `volume` + `amix normalize=0` | Voz + (1,6) / Voz − (0,6) |
| Reduzir ruído | `afftdn` (`audiofx.py:59`), força **fixa 8** | Reduzir ruído |
| Normalizar | `loudnorm` EBU R128, -16 LUFS / TP -1.5 / LRA 11 (`audiofx.py:69,84`) | Normalizar / melhorar |
| Analisar | `ffprobe` + `volumedetect` (`audiofx.py:97`) | Analisar |

**Cadeia:** ruído → melhoria → normalização → Demucs → mix/instrumental → 44,1 kHz.

**Falhas estruturais desta seção:**

1. **Trava a janela.** `_audio_run` (`desktop.py:254`) e `_audio_analyze`
   (`desktop.py:269`) usam `subprocess.run` **síncrono na thread da UI**, sem
   progresso e **sem botão cancelar** — ao contrário do lote
   (`_batch_worker`, `desktop.py:721`, que usa thread + `events`).
2. **Inconsistência `pythonw`.** `_audio_run` troca `pythonw.exe` por `python.exe`
   (`desktop.py:244-247`), mas `_audio_analyze` chama `sys.executable` direto
   (`desktop.py:270`) — o "Analisar" não segue a mesma regra dos demais botões.
3. **Sem prévia A/B**, sem cache de haste do Demucs, sem escolha de GPU nesta seção
   (a de dispositivo da seção 4 não afeta o áudio).
4. **Valores embutidos no código**: ruído 8, LUFS -16, preset "voz" — nenhum exposto
   na interface nem ligado ao perfil.

### B.2 Tratamento de imagens

```
aba Karaokê → LabelFrame "Composição de cena" (desktop.py:486-549)
   _scene_fx_dialog (desktop.py:1062-1121)  ← 15 parâmetros expostos
        └── imagefx.process_image (imagefx.py)
   _scene_preview → scene.render_preview (scene.py)
   lote → CLI make --scene → scene.compile_scene / validate_scene
```

**Remoção de fundo** (`imagefx.py`): `color` · `luma` · `saturation` · `flood`, com
`tolerance` (0,02–0,9) e `feather`; alpha suavizado por Gaussiana e multiplicado pelo
alpha original. **Sem matting por IA** — só fundos planos.

**Ajustes:** rotação, espelhar, brilho, contraste, saturação, nitidez, P&B, sépia,
desfoque, opacidade, sombra. Saída PNG RGBA.

**Falhas estruturais desta seção:**

1. **Texto não aparece na prévia.** `scene.render_preview` filtra
   `if layer.get("type") != "image" ... continue` — camadas de texto são **ignoradas**
   na prévia; só aparecem no vídeo final. O usuário ajusta posição de texto às cegas
   (só o retângulo do canvas mostra).
2. **Canvas fixo 480×270** (`desktop.py:513`) estica qualquer formato; e a **grade**
   usa `scale = 480/1920` fixo (`desktop.py:821`) enquanto as camadas usam
   `480/width` (`desktop.py:838`) → em `1080x1920` e `1080x1080` a grade está errada.
3. **Funcionalidades ocultas:** existem no código mas **não estão no diálogo**:
   `tint`/`tint_strength` (`imagefx.py:112-114`), `shadow_offset`/`shadow_blur`
   (`imagefx.py:123-124`), `ken_burns` (`scene.py:193`) e `tint` listado em
   `scene.py:177`.
4. **Sem cache** da imagem processada — cada prévia reprocessa o efeito inteiro.
5. `flood` remove objetos encostados na borda; sombra corta nas bordas; sem
   correção EXIF.

### B.3 Validação

* `compileall` do pacote: **OK**.
* Interface com 5 abas e aba Karaokê embutida: **OK**.
* Testes automatizados de áudio/imagem: **nenhum** (ver A.4).

---

## C. Prioridades consolidadas (atualizadas)

**P0 — destrava o que está quebrado**
1. Copiar os 11 módulos que faltam em `app\` (Parte I §4.1).
2. Eliminar a duplicação `app\` × `site-packages` e fazer os 3 `.exe` de
   `python\Scripts\` apontarem para o código novo (A.2).
3. Escolher **uma** interface como produto e remover a outra do fluxo — hoje o
   webapp é quem está em uso real (A.1).

**P1 — trava a interface**
4. Áudio em thread com progresso e cancelar, reaproveitando o padrão já existente
   em `_batch_worker`/`_poll_events` (B.1.1).
5. Unificar `sys.executable` × `pythonw` num único helper (B.1.2).
6. Corrigir o bug do idioma que apaga o perfil (Parte I §4.4.1).

**P2 — entendimento do usuário**
7. Definir PROJETO / PERFIL / CONFIGURAÇÃO (Parte I §6) e implementar a tela de
   projetos + "Salvar neste perfil".
8. Um único sistema de idioma, com `ko` completo ou removendo o coreano do seletor (A.5).
9. Texto na prévia da cena; grade e canvas adaptados ao formato (B.2.1, B.2.2).
10. Expor `tint`/sombra/Ken Burns no diálogo de efeitos (B.2.3).

**P3 — higiene e documentação**
11. Remover `karaoke.py.bak`, `__pycache__`, módulos órfãos (A.3, A.6).
12. Criar testes mínimos (alinhamento, `compile_scene`, `imagefx`, perfis) (A.4).
13. Reescrever `LEIA-ME.txt`; criar `INSTALACAO_E_MODULOS.md` e `LEIA-ME-Vilm.txt`;
    reconciliar preset 400 ms × perfil 600 ms.
14. Recriar o atalho e commitar/pushar o que está só local.
