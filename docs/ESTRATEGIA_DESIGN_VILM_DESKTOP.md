# Estratégia de design — Vilm Desktop (interface única)

## ESTADO FINAL 27/11/2026

> Carimbo deste topo: **27/11/2026**. O corpo histórico a partir de
> "Decisões travadas" foi escrito em 26/11/2026 e anterior; suas referências
> `arquivo:linha` são registro da época, **não prova do estado atual**.
> Nelas, onde se lia `arquivo.py:NNN`, passa a valer a leitura
> `módulo → função` indicada nesta seção.

### O que já é real (com prova no código entregue)

| Tema | Estado em 27/11/2026 | Onde ver |
|---|---|---|
| Casca | 7 abas planas e cabeçalho Projeto/Perfil/Idioma; lazy em Produção/Cena/Áudio | `tkinter_app.py → TAB_SPECS, VilmTkApp._on_tab_changed, VilmTkApp._build_header` |
| Abas | Projetos, Produção, Legendas (Editor de cues), Cena, Áudio, Configurações, Assistente | `tk_projects_tab → ProjectsTab`, `tk_production_tab → ProductionTab`, `tk_cue_editor → CueEditorTab`, `tk_scene_tab → SceneTab`, `tk_audio_tab → AudioTab`, `tk_settings_tab → SettingsTab`, `tk_assistant_tab → AssistantTab` |
| Projeto | pasta + `projeto.json` aditivo; `letra.txt`, `FUNDO*`, `PLAYBACK*`, `saida\`, `trabalho\` preservados | `project.py → write_project_meta, update_project_meta, save_project_meta`; `project_context.py → ProjectContext.activate` |
| Perfil | CRUD na aba Configurações | `tk_settings_tab → _new_profile, _duplicate_profile, _save_profile_settings, _delete_profile`; `profiles_api → create_profile, update_profile, set_active_profile` |
| Configuração | um dono: `settings.json` | `settings.py → load_settings, update_settings, migrate_legacy_settings` |
| Idioma | um funil, 492 chaves em pt/en/ko, retradução imediata | `tkinter_app.py → VilmTkApp.apply_language`; `i18n → LANGUAGES, retranslate, t` |
| Módulos antes faltantes | presentes em `app\lyrics_aligner\` | `assistant_helpers, segment_realign, burn_lock, staging, export_formats, metrics, inspect_ass, compare_versions, cross_check, diff_timelines, verify_frames` |
| Atalho | existe em `C:\Users\globaltec\OneDrive\Desktop` | `Vilm Lyrics Aligner.lnk → wscript.exe → iniciar_tk.vbs` |
| Lançador | `iniciar_tk.vbs` com `PYTHONPATH=app` e `pythonw.exe -m lyrics_aligner.tkinter_app` | conteúdo literal em `docs\INSTALACAO_E_MODULOS.md` |
| CLI | 8 subcomandos | `cli.py → build_parser`: `align, audio, demo, karaoke, make, profiles, publish, waveform` |
| Serviços | alinhamento único, karaoke, cena, áudio e efeitos | `services/align → generate_alignment`, `karaoke_jobs → run_project`, `scene → render_preview`, `audiofx → apply_audio_fx`, `imagefx → process_image` |
| Cena | prévia mostra texto; autosave por projeto | `scene.py → render_preview`; `tk_scene_tab → _scene_autosave_path, _scene_preview` |
| Produção | prévia ainda usa frases fixas; QC AVISA, NAO BLOQUEIA | `tk_production_tab → _update_preview, _append_qc`; `karaoke_jobs → qc_report` |
| Webapp | `webapp.py` removido do pacote e do repositório em 27/11/2026; código arquivado em `_legado` na instalação local; `VilmPipeline.exe` permanece como legado fora do fluxo | `git ls-files` sem `webapp.py`; `VilmPipeline.exe` fora do fluxo |
| Testes | nenhum teste automático; GUI não validada por suíte | ausência de `test_*.py` em `app\` |

### Metas antigas concluídas em 27/11/2026

- **Concluído em 27/11/2026:** copiar os 11 módulos que faltavam em `app\`.
- **Concluído em 27/11/2026:** shell com 7 abas + cabeçalho Projeto/Perfil/Idioma.
- **Concluído em 27/11/2026:** `settings.py` como dono de `settings.json`.
- **Concluído em 27/11/2026:** CRUD de perfis na aba Configurações.
- **Concluído em 27/11/2026:** `projeto.json` no modelo de pasta, de forma aditiva.
- **Concluído em 27/11/2026:** um único i18n com `ko` completo (492 chaves).
- **Concluído em 27/11/2026:** áudio em `run_job` com progresso/cancelar
  (`tk_audio_tab → _audio_worker, _audio_cancel`).
- **Concluído em 27/11/2026:** atalho "Vilm Lyrics Aligner" na área de trabalho.
- **Concluído em 27/11/2026:** documentação reescrita/criada
  (`LEIA-ME.txt`, `DESIGN_FUNCIONAL.md`, `INSTALACAO_E_MODULOS.md`,
  `LEIA-ME-Vilm.txt`) e erratas nos relatórios históricos.
- **Concluído em 27/11/2026:** commit 7daf80e (Fases 0-4) e commit b484344 (remoção do webapp), ambos publicados em origin/master. **Não concluído / sem prova:** suíte de testes automáticos.

### Limitações que continuam abertas

A prévia de Produção usa frases fixas; a prévia de Cena mostra texto.
O QC **AVISA, NAO BLOQUEIA**. Não se afirma que o instalador preserva
projetos: não há prova para essa afirmação. Não se afirma "100% funcional"
sem execução de GUI. `webapp.py` removido do pacote e do repositório em 27/11/2026; código arquivado em `_legado` na instalação local; `VilmPipeline.exe` permanece como legado fora do fluxo.

---

> Objetivo: **deixar tudo funcional**, fatiando o programa em unidades que têm
> **uma função** e **uma configuração**, cada uma respondendo as mesmas perguntas:
> *o que é? · para que serve? · como aplicar hoje? · qual a melhor forma de aplicar?*
>
> Documento derivado de `RELATORIO_ESTRUTURA_VILM_DESKTOP.md` (Parte I e II).

---

## 0. Decisões travadas

| # | Decisão |
|---|---|
| D1 | **Produto = só o desktop Tk.** O webapp vira biblioteca interna (apenas `probe_media`/`DEFAULT_SETTINGS` transitórios), menu removido, `vilm-webapp.exe` descontinuado. |
| D2 | **PROJETO = pasta de hino + `projeto.json` gravado automaticamente.** Formato das pastas existentes (`letra.txt`, `FUNDO*`, `PLAYBACK*`, `saida\`) **não muda**. |
| D3 | **PERFIL é criado e editado na própria interface.** `profiles.json` passa a ser editado só pelo programa. |
| D4 | **Ordem: camada de dados → depois as telas.** |

---

## 1. Princípios de design

1. **Uma fonte de verdade por conceito.** Cada conceito tem **um** arquivo que lê e
   escreve. Mais ninguém abre JSON com `json.load` solto.
2. **Tela não guarda estado.** A tela lê do modelo e escreve no modelo. Se o dado morre
   quando a janela fecha, a tela está errada (é o que acontece hoje em
   `tk_settings_tab`).
3. **Todo trabalho longo usa o mesmo contrato:** roda em thread, reporta progresso via
   fila, é cancelável. Já existe e está certo em `_batch_worker` + `_poll_events`
   (`VilmTkApp._poll_events`) e em `process_runner.run_streaming_process`. O áudio precisa
   passar a usá-lo.
4. **Toda configuração tem dono, caminho único e botão de salvar.**
5. **Domínio ≠ tela.** Um serviço é usado por 2 telas **e** pela CLI; nunca pelo
   contrário.
6. **Um único i18n.** `i18n.t()` manda em tudo; o dicionário `TEXT` privado do
   `desktop.py` morre.
7. **Compatibilidade preservada:** não renomear pasta, arquivo de projeto nem
   `history.csv`/`qc.json` que já existem.

---

## 2. As fatias — as mesmas perguntas para cada coisa

### BLOCO A — Fundação (sem isto, nada acima funciona)

#### A1 · EXECUÇÃO (runtime)

| | |
|---|---|
| **O que é** | O jeito único de o programa chamar processos externos (Python CLI, FFmpeg, Demucs). |
| **Para que serve** | Garantir que todo botão demorado: (a) use o interpretador certo, (b) mostre progresso, (c) possa ser cancelado, (d) não trave a janela. |
| **Função** | `run_job(cmd, on_progress, cancel_event)` — um contrato só. |
| **Configuração** | `python_executable` (auto: `pythonw`→`python`), `PYTHONPATH=app`, `PYTHONIOENCODING=utf-8`, timeout. Guarda em `settings`. |
| **Hoje** | Concluído em 27/11/2026: `tk_production_tab._alignment_worker → process_runner.run_streaming_process`; lote em thread com eventos (`tk_production_tab._batch_worker`); áudio em `process_runner.run_job` com progresso e cancelar (`tk_audio_tab._audio_worker`, `_audio_cancel`). |
| **Como aplicar** | Criar a função `run_job` (encapsulando `process_runner`) e **todos** os serviços passarem por ela. `subprocess.run` direto na UI deixa de existir. Ao final, os botões de áudio ganham barra de progresso + cancelar, iguais aos do lote. |
| **Aceite** | Clicar em "Extrair instrumental" com a janela minimizada/responsiva; "Cancelar" interrompe e limpa a saída parcial. |

#### A2 · CONFIGURAÇÃO

| | |
|---|---|
| **O que é** | As preferências da **máquina e do usuário**: idioma, tema, janela, dispositivo, pasta raiz de projetos, padrões, perfil ativo, nomenclatura. |
| **Para que serve** | Uma vez escolhido, vale para todos os projetos e todas as telas. |
| **Função** | Ler/escrever **um** arquivo; expor `settings.get/set/save`. |
| **Configuração** | Ela própria. Caminho único: **`%LOCALAPPDATA%\LyricsAligner\settings.json`** (lido por `settings.py → load_settings`). |
| **Hoje** | Concluído em 27/11/2026: `settings.py → load_settings, update_settings, migrate_legacy_settings` é o dono de `settings.json`; os quatro arquivos antigos são fontes de migração única. A aba Configurações tem Salvar/Restaurar e trocar idioma não apaga perfil (`tkinter_app.py → VilmTkApp.apply_language`). |
| **Como aplicar** | Novo módulo `settings.py`; absorve os 3 arquivos menores; `desktop-preferences` migra uma vez e some. Aba Configurações ganha **Salvar / Restaurar padrão**. Corrigir a escrita para sempre *mesclar*. |
| **Aceite** | Fechar e reabrir o programa mantém idioma, tema, dispositivo, perfil e pasta de projetos. |

#### A3 · PERFIL

| | |
|---|---|
| **O que é** | Um **preset de estilo + tempo + acabamento de um evento** (ex.: "Festa do IBUC 2026"). Não é projeto, não guarda mídia. |
| **Para que serve** | Trocar de evento = trocar de perfil, e **tudo** o que é gerado já sai no estilo certo, sem refazer ajustes. |
| **Função** | CRUD de perfis + `apply(profile) → settings de geração`. |
| **Configuração** | `max_chars`, `lead_ms`, `end_pad_ms`, durações, fonte/cor/borda/margens, linhas, `stanza_mode`, transição, fundo, resolução, preset de encode, intro/logo (`extras`), descrição. **`projects_root` sai daqui** (vira configuração A2). |
| **Hoje** | Concluído em 27/11/2026: `profiles_api → load_profiles, create_profile, duplicate_profile, save_profile_settings, set_active_profile, delete_profile`; CRUD em `tk_settings_tab → _new_profile, _duplicate_profile, _save_profile_settings, _delete_profile`, com painel "o que este perfil muda". |
| **Como aplicar** | Tira o CRUD de dentro de `karaoke_jobs` para um módulo próprio (ou mantém lá, mas expondo API). Aba **Configurações** ganha: lista de perfis + **Novo / Duplicar / Salvar ajustes aqui / Excluir**, e um painel **"O que este perfil muda"** (gerado a partir do próprio JSON). **Todos** os caminhos de geração recebem `--profile`/`profile=`. |
| **Aceite** | Criar um perfil pela interface, trocar um valor, salvar, gerar um vídeo de outro hino e ver o valor aplicado — sem tocar em JSON. |

#### A4 · PROJETO

| | |
|---|---|
| **O que é** | **Um hino**: uma pasta com `letra.txt`, a mídia cantada, `FUNDO*`, `PLAYBACK*`, imagem de título e `saida\`. Grava **automaticamente** `projeto.json` nessa pasta. |
| **Para que serve** | Guardar entre sessões: mídia, playback, faixa processada, perfil usado, cena, referências. É a "memória" do programa. |
| **Função** | `discover(pasta)`, `open(pasta)`, `save()`, `field=…`. Único ponto que lê/ escreve `projeto.json`. |
| **Configuração** | **Do projeto** (não é config global): `media`, `playback`, `lyrics_file`, `range_start/end`, `profile`, `scene_file`, `outputs[]`. |
| **Hoje** | Concluído em 27/11/2026: `project_context → ProjectContext.discover, activate` e `project.py → write_project_meta, update_project_meta, save_project_meta`. Projeto = pasta + `projeto.json` aditivo; saídas ficam em `<pasta>\saida`. |
| **Como aplicar** | Evoluir `project.py`: `HymnProject` ganha mídia/playback/faixa/perfil/cena e passa a ser **lido/salvo sozinho** ao abrir/gerar. Descoberta automática de conteúdo pela convenção já usada (`letra.txt`, `FUNDO*`, `PLAYBACK*`) — **zero mudança nas pastas antigas**. |
| **Aceite** | Abrir um hino de 2026, trocar a cor, gerar, fechar, reabrir → tudo continua lá. |

#### A5 · IDIOMA

| | |
|---|---|
| **O que é** | Um dicionário de tradução único com troca em tempo real. |
| **Para que serve** | O usuário entende o programa no idioma que escolheu — **inteiro**, não só uma aba. |
| **Função** | `t(chave)` + `set_language()`; um só. |
| **Configuração** | `language` (guarda em A2). |
| **Hoje** | Concluído em 27/11/2026: `i18n → LANGUAGES` tem **492 chaves em pt/en/ko**; `tkinter_app.py → VilmTkApp.apply_language` é o único funil e chama `i18n.retranslate(root)` para as 7 abas. |
| **Como aplicar** | Completar `ko` em `i18n`; migrar os ~42+37 rótulos do `desktop.py` para `i18n`; apagar o `TEXT`; **um** seletor no topo da janela que chama `set_language()` e redesenha **todas** as abas. |
| **Aceite** | Escolher 한국어 e ver as 7 abas em coreano; trocar a qualquer momento sem perder perfil. |

#### A6 · DESACOPLAR O WEBAPP

| | |
|---|---|
| **O que é** | Tirar o Flask do caminho mantendo só o que é usado como biblioteca. |
| **Para que serve** | Um produto, uma interface, um lançador — eliminar o "dois programas" que confunde. |
| **Função** | Nenhuma nova: **mover** o que é usado e apagar o resto. |
| **Configuração** | `webapp-config.json` migra para `settings` (é só `projects_root`). |
| **Hoje** | `tkinter_app.py → VilmTkApp._build_menu` não expõe mais "Abrir interface web"; `settings.py → migrate_legacy_settings` absorve os arquivos antigos. `webapp.py` removido do pacote e do repositório em 27/11/2026; código arquivado em `_legado` na instalação local; `VilmPipeline.exe` permanece como legado fora do fluxo. |
| **Como aplicar** | `DEFAULT_SETTINGS` → `settings.py`; `probe_media` → `media.py`; remover o menu e `_open_webapp`; `webapp.py` removido do pacote e do repositório em 27/11/2026; código arquivado em `_legado` na instalação local; `VilmPipeline.exe` permanece como legado fora do fluxo; `vilm-webapp.exe` descontinuado. |

---

### BLOCO B — Serviços de domínio

> Todos seguem o mesmo molde: **entra** arquivo + config → **roda** via `run_job` →
> **sai** artefato no projeto → **reporta** progresso e QC.

#### B1 · ALINHAMENTO

| | |
|---|---|
| **O que é** | Descobrir **quando** cada linha é cantada (Whisper + Demucs + regras 600/500/40-60). |
| **Para que serve** | É o coração: sem tempo certo, nada de karaokê. |
| **Função** | `align(media, lyrics, range) → cues`. |
| **Configuração** | Do perfil (A3) por omissão, com override manual: `mode`, `language`, `model`, `separate`, `max_chars`, `lead_ms`, `end_pad_ms`, `min/max_duration`, `min_gap`, faixa. |
| **Hoje** | `services/align → generate_alignment` é o ponto de geração de `saida\lyrics.srt`; `tk_production_tab → _alignment_worker` usa `process_runner.run_streaming_process`; `tk_pipeline_tab → PipelineTab(tools_only)` não tem mais fluxo próprio de alinhamento. |
| **Como aplicar** | **Um único caminho: o CLI `align` via `run_job`**, com perfil aplicado e saída dentro do projeto (`<pasta>/trabalho/align.srt`). A aba Legendas consome esse mesmo arquivo. |
| **Aceite** | Dois hinos alinhados saem com as mesmas regras de tempo, e o SRT aparece no editor de cues sem importar nada à mão. |

#### B2 · CUES / LEGENDAS

| | |
|---|---|
| **O que é** | O modelo editável dos tempos (`CueTiming`) + o editor. |
| **Para que serve** | Consertar o que o Whisper errou (linha fora de tempo, corte ruim, texto). |
| **Função** | CRUD de cues com undo/redo/validação. |
| **Configuração** | Regras de validação (duração mín/máx, gap, ordem) — vem de A2/A3. |
| **Hoje** | `project.CueTiming` + `tk_cue_editor` (bom) ✅. Mas autosave em arquivo próprio, desconectado do projeto; realign precisa de `segment_realign` **que não existe em `app\`** ❌. |
| **Como aplicar** | O editor carrega/salva **do projeto** (A4). Copiar `segment_realign`. `--realign` do `make` passa a ser o mesmo botão. |

#### B3 · KARAOKÊ (ASS progressivo)

| | |
|---|---|
| **O que é** | Transformar tempos em legenda animada (`\kf`, rolagem, estrofes). |
| **Para que serve** | O "olho" do vídeo: letra preta suave, linha no topo, fade de saída. |
| **Função** | `render_ass(cues, lyrics, style) → .ass`. |
| **Configuração** | Estilo inteiro do perfil (A3): fonte, cores, contorno/sombra, margens, linhas, `stanza_mode`, `transition`, `transition_ms`. |
| **Hoje** | `karaoke.py → render_karaoke_ass` e `karaoke_jobs → make_style` ✅. A prévia de Produção (`tk_production_tab → _update_preview`) ainda desenha frases fixas de exemplo; é uma limitação conhecida. |
| **Como aplicar** | Prévia da aba Produção passa a usar `karaoke.render` sobre 2–3 cues reais do projeto, em vez das frases fixas. |

#### B4 · PRODUÇÃO (vídeo final)

| | |
|---|---|
| **O que é** | Montagem: fundo + ASS queimado + intro + logo + transições + encode. |
| **Para que serve** | Entregar o MP4 pronto para postar. |
| **Função** | `produce(project) → mp4` (é o CLI `make`). |
| **Configuração** | Perfil (intro/logo/fundo/resolução/preset/CRF) + cena (B5) + transição. |
| **Hoje** | `karaoke_jobs → run_project, _build_final` ✅; `tk_production_tab → _batch_worker` gera o lote com perfil, cena e transição. |
| **Como aplicar** | Ganhar botão **"Gerar vídeo deste projeto"** na aba Produção (usa o mesmo `make`), além do lote. |

#### B5 · CENA

| | |
|---|---|
| **O que é** | Camadas de imagem/texto com posição, tempo e fade, salvas em JSON. |
| **Para que serve** | Marca d'água, logo, título, crédito — sem mexer em editor externo. |
| **Função** | Modelo + validação + compilação para FFmpeg. |
| **Configuração** | **Da cena** (`cena.json` por projeto): camadas, `safe_margin`, preset. Autosave **por projeto**, não global. |
| **Hoje** | `scene.py → render_preview, compile_scene, validate_scene` ✅; `tk_scene_tab → _scene_preview` **mostra texto** e `_scene_autosave_path` grava autosave **por projeto** em `trabalho\scene-autosave.json`. Canvas/grade fora de 1920×1080 seguem sem verificação automatizada. |
| **Como aplicar** | Canvas derivado do formato (`w/h` proporcionais), grade na mesma escala das camadas, autosave em `<pasta>/trabalho/cena-autosave.json`, e `render_preview` **renderizando texto** (hoje `if type != "image": continue`). |

#### B6 · EFEITOS DE IMAGEM

| | |
|---|---|
| **O que é** | Remoção de fundo + ajustes numa imagem de camada. |
| **Para que serve** | Deixar logo/fundo pronto para a cena sem abrir Photoshop. |
| **Função** | `process_image(in, out, fx)`. |
| **Configuração** | **Da camada** (`fx`): método (`color`/`luma`/`saturation`/`flood`), `key_color`, `tolerance`, `feather`, brilho, contraste, saturação, nitidez, blur, opacidade, rotação, P&B, sépia, espelhar, sombra — **e os que existem mas não estão na tela:** `tint`/`tint_strength` (`imagefx → apply_adjustments`), `shadow_offset`/`shadow_blur` (`imagefx → apply_adjustments`), `ken_burns` (`scene → compile_scene`). |
| **Hoje** | `tk_scene_tab → _scene_fx_dialog` expõe 15 parâmetros; segue sem validação de tipo de camada e sem cache de imagem processada. |
| **Como aplicar** | Expor os 3 ocultos, validar `type == "image"`, cachear `preview_fx_<i>.png` por hash do `fx`. |

#### B7 · ÁUDIO

| | |
|---|---|
| **O que é** | Análise, separação voz/comp, ganhos, ruído, normalização. |
| **Para que serve** | Playback a partir do cantado, voz mais presente, áudio limpo. |
| **Função** | `analyze / split / gain / denoise / normalize / enhance`. |
| **Configuração** | **Do áudio** (agora embutida no código): ganho de voz (1,6 / 0,6 fixos na UI), força do `afftdn` (**8 fixo** em `tk_audio_tab → _audio_defaults`), `-16 LUFS / TP -1.5 / LRA 11` (`audiofx → normalize`), preset `voz`/`musica`, diretório de trabalho. → viram campos da aba + defaults do perfil. |
| **Hoje** | `audiofx → analyze, separate_audio, mix_audio, denoise, normalize, enhance` ✅; `tk_audio_tab → _audio_worker` usa `process_runner.run_job` com progresso e cancelar, e `_audio_defaults` expõe campos editáveis. Sem prévia A/B e sem cache do Demucs. |
| **Como aplicar** | Passar tudo para `run_job` (A1); ganhos/ruído/LUFS viram campos com defaults do perfil; saída em `<pasta>/trabalho/audio/` e registrada no `projeto.json`. |

#### B8 · QC / PREFLIGHT

| | |
|---|---|
| **O que é** | Verificação antes de renderizar: canto sem legenda, pausa instrumental longa, camada fora da área segura, arquivo ausente. |
| **Para que serve** | Pegar o erro **antes** de 10 minutos de encode. |
| **Função** | `qc(project) → avisos[]`. |
| **Configuração** | Limiares: `min_vocal` (hoje 1,2 s em `karaoke_jobs → qc_report`), pausa instrumental, margem da área segura. |
| **Hoje** | `qc_report` ✅, `qc.json` ✅, mostra na tela ✅ — mas **só no lote**; geração individual não mostra nada. |
| **Como aplicar** | Concluído em 27/11/2026 no que cabe ao fluxo: QC antes de "Gerar vídeo" e exibição na aba Produção. O QC **AVISA, NAO BLOQUEIA**; não há bloqueio. |

#### B9 · LOTE

| | |
|---|---|
| **O que é** | Fila de hinos processados em sequência. |
| **Para que serve** | Uma Festa = 20 hinos numa noite. |
| **Função** | `queue[project] → for each: produce()`. |
| **Configuração** | Perfil, intro/logo/fundo, transição, template de nome. |
| **Hoje** | `tk_production_tab → _batch_worker` ✅ é o lote; `tk_pipeline_tab → PipelineTab(tools_only)` ficou sem lote concorrente, embutido em Legendas. |
| **Como aplicar** | **Um só**: o do desktop. O "Batch (pasta)" da aba Pipeline vira "Adicionar pasta" do lote (`tk_production_tab → _add_folder`). |

#### B10 · SAÍDA / NOMENCLATURA

| | |
|---|---|
| **O que é** | Onde e com que nome os arquivos são gravados. |
| **Para que serve** | Achar o arquivo depois e não poluir o usuário. |
| **Função** | `resolve_output(project, template) → caminho`. |
| **Configuração** | `output_template` (hoje `{pasta}`, em `tk_production_tab → ProductionTab`), pasta base, subpasta de trabalho. |
| **Hoje** | **Quatro destinos**: `<pasta>/saida/` (lote), `Documents\Vilm Lyrics Aligner\` (Gerar SRT, `:1297`), `<mídia>_lyrics/` (`tk_pipeline_tab:113`), `<áudio>_saida/` (`:633`). |
| **Como aplicar** | **Um destino:** o projeto. `Documents\` deixa de existir; as duas pastas `_lyrics`/`_saida` passam a ser `<pasta>/trabalho/`. |

#### B11 · HISTÓRICO

| | |
|---|---|
| **O que é** | Registro do que foi gerado, quando, com qual perfil e com quais avisos. |
| **Para que serve** | Responder "por que esse vídeo ficou assim?" e refazer igual. |
| **Função** | `append(project, outputs, warnings)`. |
| **Configuração** | Formato (mantido `history.csv`, `;`, UTF-8-BOM). |
| **Hoje** | `tk_production_tab → _record_history, _update_project_outputs` ✅; gravado em `history.csv` e em `projeto.json` para SRT e lote. |
| **Como aplicar** | Chamado por **toda** geração (individual, lote, áudio), gravando também em `projeto.json` (`last_build`). |

---

### BLOCO C — Telas

> Cada tela: **um** tema, lê/ escreve nos modelos, **nada** de trabalho pesado inline.

#### C1 · SHELL (janela e abas)

| | |
|---|---|
| **O que é** | A casca: menu, barra de status, **7 abas** e o seletor de idioma. |
| **Para que serve** | Dar acesso previsível a tudo — sem segundo nível escondido. |
| **Função** | Montar `Notebook`, tema, atalhos globais, barra de progresso global. |
| **Configuração** | Janela (largura/altura/tema), idioma, aba inicial. |
| **Hoje** | 5 abas (`tkinter_app:117-121`) e a aba Karaokê é um `Frame` **sem scroll** com 4 LabelFrames → conteúdo cortado em 1100×720. |
| **Como aplicar** | 7 abas **planas** (nenhuma interna — sub-abas escondem, foi o que gerou a confusão). Ver §3. |

#### C2 · PROJETOS · C3 · PRODUÇÃO · C4 · CENA · C5 · ÁUDIO · C6 · CONFIGURAÇÕES · C7 · LEGENDAS · C8 · ASSISTENTE

| Tela | O que é | Para que serve | Função | Configuração | Hoje | Como aplicar |
|---|---|---|---|---|---|---|
| **C2 Projetos** | Lista de pastas de hino + estado de cada (letra? mídia? alinhado? gerado? avisos) | Escolher o que vou trabalhar | Abrir/criar/descobrir projeto | **Nova** (hoje não existe) | Não existe — fica dentro da aba Pipeline | Nova aba; usa A4; absorve `/api/projects` que só o webapp tinha (A6) |
| **C3 Produção** | mídia + faixa + letra + perfil + gerar SRT + gerar vídeo + QC + lote | O fluxo do dia a dia | Orquestra B1, B3, B4, B8, B9 | Perfil (A3), faixa, template, intro/logo/fundo/transição | 4 blocos amontoados na aba "Karaokê" sem scroll; "Gerar SRT" ≠ "Gerar vídeos"; perfil só afeta o lote | Uma tela, com **abas internas apenas aqui** (SRT / Vídeo / Lote) **ou** seções com scrollbar; perfil aplicado a tudo |
| **C4 Cena** | Canvas + lista + propriedades + timeline + efeitos | Compor imagem/texto | Editar `cena.json` | Camada, formato, `safe_margin`, snap/grade, efeitos | Dentro de C3, cortado; grade errada fora de 1920×1080; texto não aparece na prévia | Aba própria; canvas derivado do formato; `render_preview` com texto; autosave por projeto |
| **C5 Áudio** | Analisar / instrumental / voz± / ruído / normalizar | Preparar áudio | Chama `audiofx` via `run_job` | Ganho de voz, força de ruído, LUFS, preset, GPU | Dentro de C3, **síncrono e sem cancelar**; valores fixos no código | Aba própria; progresso+cancelar; campos editáveis com defaults do perfil |
| **C6 Configurações** | Máquina + perfis (CRUD) + padrões de alinhamento/estilo/saída + idioma | Definir "como o programa é" | Lê/escreve A2 e A3 | **Tudo** (ver §4) | Formulário **sem salvar**, sem perfil, base no `DEFAULT_SETTINGS` do webapp | Aba própria com **Salvar/Restaurar** + cartão de perfil com "o que muda" + Novo/Duplicar/Salvar aqui/Excluir |
| **C7 Legendas** | Tabela de cues, undo/redo, validação, realign | Consertar tempos | Edita B2 | Regras de validação | Bom, mas autosave solto e `segment_realign` ausente | Ligada ao projeto; copiar `segment_realign` |
| **C8 Assistente** | Refrões, ortografia, tradução | Ajudar a letra | Chama `opencode_bridge` | Modelo, idioma-alvo | **Quebrado** — `assistant_helpers` não existe em `app\` | Copiar `assistant_helpers`; manter por último (é o menor ganho) |

---

### BLOCO D — Entrega

#### D1 · CLI

| | |
|---|---|
| **O que é** | `python -m lyrics_aligner {profiles, karaoke, make, audio, align, publish, waveform}`. |
| **Para que serve** | Automatizar/testar fora da interface; é o que a UI chama. |
| **Função** | Espelhar **exatamente** os serviços do Bloco B. |
| **Configuração** | `--profile` em **todos** os que geram algo; saída no projeto. |
| **Hoje** | Completo e funcional ✅ (verificado). Só `align` e `publish` ainda não receberam `--profile` de forma obrigatória. |
| **Como aplicar** | `align` ganha `--profile`; `--output-dir` padrão vira a pasta do projeto. |

#### D2 · LANÇADOR E ATALHOS

| | |
|---|---|
| **O que é** | O jeito do usuário abrir o programa. |
| **Para que serve** | Um clique e está no programa certo. |
| **Função** | Iniciar `tkinter_app` com `PYTHONPATH` correto. |
| **Configuração** | — |
| **Hoje** | `iniciar_tk.vbs` ✅ correto; **nenhum atalho** em Desktop Público/Usuário/Iniciar ❌; `python\Scripts\vilm-lyrics-aligner.exe` aponta para a **cópia antiga** sem perfis/cena ❌; `vilm-webapp.exe` abre o webapp que D1 descontinua. |
| **Como aplicar** | Recriar atalho `.lnk` → `iniciar_tk.vbs` com ícone `vilm.ico`; **reinstalar o pacote** para que os `.exe` apontem para `app\`; remover `vilm-webapp.exe`. |

#### D3 · DOCUMENTAÇÃO

| | |
|---|---|
| **O que é** | O manual do usuário e do módulo. |
| **Para que serve** | Responder sozinho "o que é, pra que serve, como aplicar" — as 4 perguntas deste documento. |
| **Função** | — |
| **Configuração** | — |
| **Hoje** | `LEIA-ME.txt` **descreve a versão web** (bandeja/navegador) e um preset contraditório (lead 400 ms, 3 linhas, letra branca); `DESIGN_FUNCIONAL.md` existe mas mostra sub-abas que não existem; `INSTALACAO_E_MODULOS.md` e `LEIA-ME-Vilm.txt` **não foram criados**. |
| **Como aplicar** | Reescrever o `LEIA-ME.txt` para as 7 abas; criar os dois docs faltantes; o `DESIGN_FUNCIONAL.md` passa a espelhar §3. |

---

## 3. Mapa da interface alvo (7 abas planas)

```
Vilm Lyrics Aligner
├── 1. Projetos      ← A4  : lista de pastas, abrir/criar, status, avisos   (NOVA)
├── 2. Produção      ← B1 B3 B4 B8 B9 : perfil, mídia, letra, faixa,
│                                        gerar SRT, gerar vídeo, QC, lote
├── 3. Legendas      ← B2  : editor de cues
├── 4. Cena          ← B5 B6 : canvas, timeline, propriedades, efeitos     (SAIU de Produção)
├── 5. Áudio         ← B7  : analisar, instrumental, voz, ruído, normalizar (SAIU de Produção)
├── 6. Configurações ← A2 A3 : perfis (CRUD), alinhamento, estilo, saída,
│                              idioma, tema, dispositivo, pasta de projetos
└── 7. Assistente    ← C8  : refrões, ortografia, tradução
```

* **Por que planas:** a aba "Karaokê" virou 4 blocos empilhados sem scroll justamente
  porque a informação era demais para um bloco só; sub-abas dentro de aba escondem
  função. Sete abas visíveis = cada coisa com seu nome.
* O cabeçalho fixo (acima do `Notebook`) fica só com: **Projeto atual** + **Perfil**
  + **Idioma** — os três conceitos que o usuário precisa ver o tempo todo.

---

## 4. Matriz de configuração — dono, caminho, botão

| Config | Guarda em | Quem escreve | Quem lê | Botão |
|---|---|---|---|---|
| Idioma, tema, janela | `config.json` | `settings.py` | todas as telas | Configurações ▸ Salvar |
| Dispositivo (CUDA/CPU) | `config.json` | `settings.py` | `run_job` | Configurações ▸ Salvar |
| Pasta raiz de projetos | `config.json` | `settings.py` | tela Projetos | Configurações ▸ Salvar |
| Perfil ativo | `config.json` | `settings.py` | Produção | seletor do cabeçalho |
| Perfis (estilo/tempo/acabamento) | `profiles.json` | `profiles` API | align/ass/produce | Configurações ▸ **Novo/Salvar aqui** |
| Padrões de alinhamento | `config.json` (defaults) | `settings.py` | align (sem perfil) | Configurações ▸ Salvar |
| Mídia, playback, faixa, cena | `<pasta>\projeto.json` | `project.py` | todas as telas | automático |
| Camadas e `fx` | `<pasta>\cena.json` | `scene.py` | Cena / produce | Cena ▸ Ctrl+S (autosave em `trabalho\`) |
| Histórico | `<pasta>\history.csv` | `history` | tela Projetos | — |
| QC | `<pasta>\trabalho\qc.json` | `qc` | Produção | — |

> Regra de ouro: **se não está nesta tabela, não pode ser gravado em arquivo nenhum.**

---

## 5. Mapa de arquivos alvo (mantendo o pacote plano)

**Novos (3):**
```
settings.py     ← A2  (absorve desktop-preferences.json, .vilm_tkinter_config.json,
                        webapp-config.json, DEFAULT_SETTINGS do webapp)
profiles_api.py ← A3  (CRUD; karaoke_jobs passa a importar dele)
job.py          ← A1  (envolve process_runner: run_job com progresso/cancel)
```
**Evoluídos (5):**
```
project.py       ← A4  (ganha mídia/playback/faixa/perfil/cena + discover/open/save)
i18n.py          ← A5  (completar 'ko')
tkinter_app.py   ← C1  (7 abas, seletor de idioma global, remove menu webapp)
tk_settings_tab.py ← C6 (usa settings + profiles; ganha Salvar)
karaoke_jobs.py  ← B4/B8 (mantém run_project/qc; delega profiles)
```
**Divididos:**
```
desktop.py (69 KB, 1474 linhas) → 4 telas:
   ui_producao.py   (C3)   ui_cena.py    (C4)
   ui_audio.py      (C5)   — o resto volta para tkinter_app
```
**Copiados de `site-packages\` (11):**
`assistant_helpers, segment_realign, burn_lock, staging, export_formats, metrics,
inspect_ass, compare_versions, cross_check, diff_timelines, verify_frames`
**Órfãos a remover (3):** `runtime_setup.py, resolve_bridge.py, cleanup.py`
**Lixo a remover:** `karaoke.py.bak-20261119`, `__pycache__`, `vilm-webapp.exe`.

---

## 6. Fases (ordem D4: dados → telas)

### Fase 0 · Destravar (sem dependência, ~1 sessão)
- [x] Concluído em 27/11/2026: copiar os **11 módulos** que faltam em `app\` → Assistente, realign, burn, staging, export voltam a funcionar
- [x] Concluído em 27/11/2026: idioma não apaga perfil (`tkinter_app.py → VilmTkApp.apply_language`; `profiles_api → set_active_profile`)
- [ ] Remover `.bak`, `__pycache__`, `runtime_setup`/`resolve_bridge`/`cleanup`
- [x] Concluído em 27/11/2026: menu "Abrir interface web" removido (`tkinter_app.py → VilmTkApp._build_menu`); `webapp.py` removido do pacote e do repositório em 27/11/2026; código arquivado em `_legado` na instalação local; `VilmPipeline.exe` permanece como legado fora do fluxo
- **Saída:** nenhum botão estoura erro.

### Fase 1 · Fundação (camada de dados)
- [x] Concluído em 27/11/2026: `settings.py` + unificação dos 4 arquivos de config + aba com **Salvar**
- [x] Concluído em 27/11/2026: `process_runner.run_job` e conversão do áudio (`tk_audio_tab → _audio_worker`)
- [x] Concluído em 27/11/2026: `profiles_api` + CRUD na aba Configurações + painel "o que este perfil muda"
- [x] Concluído em 27/11/2026: `project.py` com `projeto.json` automático + descoberta automática
- [x] Concluído em 27/11/2026: `i18n` com `ko` (492 chaves) e funil único em `VilmTkApp.apply_language`
- **Saída:** abrir → perfil, idioma, projeto e config persistem entre sessões.

### Fase 2 · Serviços (um por vez, cada um com teste mínimo)
- [x] Concluído em 27/11/2026: B1 alinhamento unificado (`services/align → generate_alignment`, saída em `saida\lyrics.srt`)
- [x] Concluído em 27/11/2026: B4 produção e B8 QC com avisos (o QC AVISA, NAO BLOQUEIA)
- [x] Concluído em 27/11/2026: B10 saída única em `<pasta>\saida` + B11 `history.csv`
- [x] Concluído em 27/11/2026: B9 "Batch (pasta)" substituído por `PipelineTab(tools_only)`
- [ ] Parcial em 27/11/2026: texto na prévia e autosave por projeto concluídos; canvas/grade fora de 1920×1080 sem verificação
- [ ] B6 efeitos: expor `tint`/sombra/Ken Burns, validar tipo, cache
- [x] Concluído em 27/11/2026: B7 áudio com `run_job`, progresso/cancelar e campos editáveis
- **Saída:** do SRT ao MP4, tudo com perfil, tudo no projeto, tudo cancelável.

### Fase 3 · Telas (montar em cima da base)
- [x] Concluído em 27/11/2026: C1 shell com 7 abas + cabeçalho Projeto/Perfil/Idioma
- [x] Concluído em 27/11/2026: C2 tela Projetos (`tk_projects_tab → ProjectsTab`)
- [x] Concluído em 27/11/2026: C3/C4/C5 separadas em `tk_production_tab`, `tk_scene_tab`, `tk_audio_tab`
- [x] Concluído em 27/11/2026: C6 Configurações com CRUD de perfis; C7 Legendas e C8 Assistente presentes nas 7 abas
- **Saída:** cada coisa com sua aba e sua configuração.

### Fase 4 · Entrega
- [ ] CLI: `--profile` em `align`, saída padrão no projeto
- [ ] Parcial em 27/11/2026: atalho `.lnk` criado; `.exe` em `python\Scripts\` ainda apontam para a cópia antiga
- [x] Concluído em 27/11/2026: documentos reescritos/criados e erratas nos relatórios históricos
- [x] Concluído em 27/11/2026: commit 7daf80e (Fases 0-4) e commit b484344 (remoção do webapp), ambos publicados em origin/master

---

## 7. Critérios de aceite globais

1. Fechar e reabrir **não perde** nada (idioma, perfil, projeto, cena, ajustes).
2. **Todo** botão demorado: mostra progresso, tem cancelar, não congela a janela.
3. Nenhum botão lança `ModuleNotFoundError`.
4. Trocar o perfil muda o resultado de **qualquer** geração.
5. Um hino novo = criar pasta → abrir → tudo descoberto sozinho.
6. Escolher idioma muda **as 7 abas**.
7. Existe **um** caminho de saída (`<pasta>/saida`) e **um** de trabalho (`<pasta>/trabalho`).
8. Só existe **um** programa para abrir (atalho → Tk), e ele é o mesmo código em qualquer forma de lançamento.

---

## 8. Riscos e o que **não** muda

**Não muda:** formato das pastas de hino · `profiles.json` (só ganha CRUD) ·
`history.csv`/`qc.json` · `cena.json` v1 · o CLI existente · `karaoke.py`,
`scene.py`, `imagefx.py`, `audiofx.py`, `karaoke_jobs.run_project` (só ganham entrada em `job.py`/`settings.py`).

**Riscos:**
| Risco | Mitigação |
|---|---|
| Portar `DEFAULT_SETTINGS` do webapp quebra a aba Configurações | Copiar e **comparar** antes de apagar a importação; `webapp.py` removido do pacote e do repositório em 27/11/2026; código arquivado em `_legado` na instalação local; `VilmPipeline.exe` permanece como legado fora do fluxo |
| Dividir `desktop.py` quebra atalhos/autosave | Mover método a método com o programa aberto a cada passo; `DesktopApp(embedded=True)` mantido só como casca durante a transição |
| Reinstalar os `.exe` perde o perfil ativo | `settings` já migrado para `config.json` **antes** da reinstalação |
| `ko` incompleto | `t()` cai para `pt`; nada quebra, só aparece texto em PT até completar |
| Perfis editáveis quebram os 5 vídeos já gerados | Manter `festa-ibuc`/`festa-genesis` intocados; criar perfis novos por **Duplicar** |
