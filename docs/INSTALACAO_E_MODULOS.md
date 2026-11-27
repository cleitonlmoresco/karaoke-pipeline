# Instalação e módulos — Vilm Pipeline (estado real em 27/11/2026)

Este documento descreve a instalação em
`C:\Users\globaltec\AppData\Local\VilmPipeline`, o lançador, a regra de
`PYTHONPATH`, os módulos de dados/serviços/interface, o CLI e o legado.
Não registra commit nem push: nada disso foi feito nesta etapa.

## 1. Papéis das pastas

| Pasta | Papel |
|---|---|
| `app\` | código-fonte atual (`app\lyrics_aligner\`). É a versão que o atalho usa. |
| `python\` | interpretador Python 3 com bibliotecas embutidas (torch, whisper, demucs, PIL, numpy, scipy etc.) e `Scripts\` com executáveis históricos. |
| `models\` | pesos locais (Whisper `small`, Demucs `htdemucs`, Silero VAD). Uso offline. |
| `tools\` | apenas `ffmpeg.exe` e `ffprobe.exe`. |
| `docs\` | documentação: manual do usuário, design funcional, relatórios históricos e estratégia. |

Na raiz também estão `iniciar_tk.vbs` (lançador), `profiles.json`, `vilm.ico`,
`VilmPipeline.exe` (legado), `unins000.*` e `LEIA-ME.txt`.

## 2. Lançador e conteúdo literal de `iniciar_tk.vbs`

O atalho "Vilm Lyrics Aligner" da área de trabalho (OneDrive\Desktop) aponta
para `wscript.exe` com este arquivo como argumento. Conteúdo literal:

```vbscript
' Lancador da interface Tk nativa do Vilm (com ferramentas de producao)
Set sh = CreateObject("WScript.Shell")
sh.Environment("PROCESS")("PYTHONPATH") = "C:\Users\globaltec\AppData\Local\VilmPipeline\app"
sh.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
sh.Run """C:\Users\globaltec\AppData\Local\VilmPipeline\python\pythonw.exe"" -m lyrics_aligner.tkinter_app", 0, False
```

O atalho tem como diretório de trabalho a raiz da instalação. O grupo
"Vilm Pipeline" no Menu Iniciar é histórico da instalação (inclui
"Desinstalar Vilm Pipeline").

### Regra do PYTHONPATH

O código ativo está em `app\lyrics_aligner\`. Para rodar qualquer módulo,
ferramenta ou CLI fora do atalho, defina:

```powershell
$env:PYTHONPATH='C:\Users\globaltec\AppData\Local\VilmPipeline\app'
```

Sem essa variável, `python -m lyrics_aligner ...` pode carregar
`python\Lib\site-packages\lyrics_aligner`, uma cópia antiga/parcial que não
contém os serviços novos. Os executáveis em `python\Scripts\` também apontam
para essa cópia. **Cuidado:** não use `pip uninstall lyrics-aligner` como
"limpeza" sem conferir antes o que será removido de `site-packages` e dos
scripts; a remoção da cópia antiga é feita de forma coordenada, não por
desinstalação casual do pacote. O atalho Tk sempre usa `app\` porque o
`iniciar_tk.vbs` define a variável no processo.

## 3. Módulos de dados

| Módulo | Função | Arquivo de que é dono |
|---|---|---|
| `settings.py` | `load_settings`, `save_settings`, `update_settings`, `get_setting`, `migrate_legacy_settings` | `%LOCALAPPDATA%\LyricsAligner\settings.json` (criado na primeira gravação); lê e migra `desktop-preferences.json`, `config.json`, `.vilm_tkinter_config.json`, `webapp-config.json` |
| `profiles_api.py` | `load_profiles`, `list_profiles`, `get_profile`, `create_profile`, `duplicate_profile`, `update_profile`, `save_profile_settings`, `set_active_profile`, `delete_profile`, `rename_profile` | `profiles.json` na raiz da instalação |
| `project_context.py` | `discover`, `activate`, `ProjectContext` | cria `projeto.json` mínimo em pastas legadas; outro módulo não deve substituir o arquivo inteiro |
| `project.py` | `HymnProject`, `save`, `load`, `read_project_meta`, `write_project_meta`, `update_project_meta`, `save_project_meta` | `<pasta>\projeto.json`, com mesclagem profunda/aditiva e preservação de BOM e chaves históricas |
| `process_runner.py` | `run_job`, `run_streaming_process`, `console_python`, `JobCancelled` | nenhum arquivo de dados; é o contrato único de execução com progresso/cancelamento |

Dono dos arquivos de dados, de forma resumida:

| Arquivo | Escrito por | Observação |
|---|---|---|
| `settings.json` | `settings.py` | destino canônico das preferências |
| `profiles.json` | `profiles_api.py` | CRUD na aba Configurações; `karaoke_jobs` consome, não edita |
| `projeto.json` | `project.py` / `project_context.py` | aditivo; não apaga `letra.txt`, `FUNDO*`, `PLAYBACK*`, `saida\`, `trabalho\` |
| `history.csv` | `tk_production_tab._record_history` | uma linha por geração, com avisos |
| `qc.json` | `services/align.generate_alignment` e `karaoke_jobs.run_project` | lido por `tk_production_tab` para exibir avisos |
| `cena.json` | `tk_scene_tab` (salvar) / `scene.py` (validar e compilar) | formato v1 de camadas |
| `trabalho\scene-autosave.json` | `tk_scene_tab._scene_autosave` | por projeto; global legado só sem projeto |

## 4. Serviços

| Módulo | Responsabilidade |
|---|---|
| `karaoke.py` | renderização do ASS com `\kf`, estilo, rolagem e estrofes |
| `karaoke_jobs.py` | `run_project`/`_build_final`: fundo, queima de legenda, intro, logo, transições, playback sincronizado e relatório de QC |
| `scene.py` | raiz de `app\lyrics_aligner\`: modelo de camadas, `validate_scene`, `compile_scene`, `render_preview`, área segura e presets |
| `imagefx.py` | remoção de fundo (`color`, `luma`, `saturation`, `flood`) e ajustes; saída PNG RGBA |
| `audiofx.py` | Demucs, ganhos, `afftdn`, `loudnorm` EBU R128, `enhance`, `analyze` |
| `services/align.py` | `generate_alignment`: único ponto de geração de `lyrics.srt`, debug e QC |

### Correção explícita sobre a pasta `services`

O pacote `services\` contém **apenas** `__init__.py` e `align.py`.
**Não existe módulo de cena dentro de `services`**: a cena é `scene.py` na
raiz de `app\lyrics_aligner\`. Qualquer documento ou código que cite um
módulo de cena sob `services` está desatualizado.

## 5. Interface

| Arquivo | Papel |
|---|---|
| `tkinter_app.py` | casca: menu, barra de status, log, cabeçalho Projeto/Perfil/Idioma, Notebook de 7 abas e funil único de idioma |
| `tk_projects_tab.py` | aba 1 — Projetos |
| `tk_production_tab.py` | aba 2 — Produção (lazy), lote, QC e histórico |
| `tk_pipeline_tab.py` | ferramentas embutidas em Legendas (`tools_only=True`) |
| `tk_cue_editor.py` | aba 3 — Legendas/Editor de cues |
| `tk_scene_tab.py` | aba 4 — Cena (lazy) |
| `tk_audio_tab.py` | aba 5 — Áudio (lazy) |
| `tk_settings_tab.py` | aba 6 — Configurações, inclusive CRUD de perfis |
| `tk_assistant_tab.py` | aba 7 — Assistente |
| `tk_tooltip.py`, `tk_video_preview.py`, `tk_advanced_settings.py`, `tk_config_window.py` | apoio de UI |
| `desktop.py` | casca/compatibilidade do antigo painel; não é a entrada da interface atual |

## 6. CLI (8 subcomandos)

`cli.py -> build_parser()` registra exatamente oito subcomandos:

```
align    audio    demo    karaoke    make    profiles    publish    waveform
```

Exemplos mínimos:

```powershell
$env:PYTHONPATH='C:\Users\globaltec\AppData\Local\VilmPipeline\app'
python -m lyrics_aligner profiles
python -m lyrics_aligner karaoke letras.srt letra.txt -o karaoke.ass --profile festa-ibuc
python -m lyrics_aligner make "hino.mp4" "letra.txt" -o "saida\hino.mp4" --profile festa-ibuc
python -m lyrics_aligner audio "hino.wav" -o "hino - instrumental.wav" --mode instrumental
```

## 7. Legado e estado da instalação

- `webapp.py` (Flask) e `VilmPipeline.exe` são **legado**, fora do fluxo atual.
  A remoção está sendo feita em paralelo; não há garantia de que já tenha
  terminado nesta etapa.
- `python\Lib\site-packages\lyrics_aligner\` é a cópia antiga; alguns
  executáveis de `python\Scripts\` apontam para ela.
- Não há **nenhum teste automático** (`test_*.py`) em `app\`. A validação
  conhecida é `compileall`, checagem de imports e uso manual com arquivos
  reais. A GUI não foi executada de ponta a ponta por uma suíte automatizada.
- A tradução tem 492 chaves em `pt`, `en` e `ko`, com um único funil em
  `VilmTkApp.apply_language` e `i18n.retranslate`.
