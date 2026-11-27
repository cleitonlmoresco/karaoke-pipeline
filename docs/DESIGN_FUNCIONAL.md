# Design funcional — Vilm Pipeline (estado real em 27/11/2026)

## Princípio

**Uma única janela desktop Tk**, com um cabeçalho fixo e sete abas planas.
Não há janela filha, não há página local servida e não há segundo nível de
abas escondido. As abas **Produção, Cena e Áudio** são construídas sob demanda
(*lazy*) no momento em que o usuário as seleciona; as outras quatro são
construídas na abertura.

O documento descreve o que o código faz hoje. Limitações conhecidas são
declaradas no fim, sem prometer funcionamento que não foi exercitado em GUI.

## Árvore da interface

```
Vilm Lyrics Aligner                      tkinter_app.py -> VilmTkApp
├── Cabeçalho fixo                       Projeto | Perfil | Idioma
└── Notebook (7 abas, nesta ordem)
    ├── 1. Projetos                      tk_projects_tab.py -> ProjectsTab
    ├── 2. Produção                      tk_production_tab.py -> ProductionTab (lazy)
    │   └── painel de ferramentas        tk_pipeline_tab.py -> PipelineTab(tools_only)
    ├── 3. Legendas                      tk_cue_editor.py -> CueEditorTab
    │                                    (rótulo atual: "Editor de cues")
    ├── 4. Cena                          tk_scene_tab.py -> SceneTab (lazy)
    ├── 5. Áudio                         tk_audio_tab.py -> AudioTab (lazy)
    ├── 6. Configurações                 tk_settings_tab.py -> SettingsTab
    └── 7. Assistente                    tk_assistant_tab.py -> AssistantTab
```

A ordem pública das abas está em `TAB_SPECS` (`tkinter_app.py`). A construção
lazy é disparada por `VilmTkApp._on_tab_changed`, que compara a identidade do
widget selecionado com `tab_production`, `tab_scene` e `tab_audio` — nunca por
índice numérico.

## Cabeçalho

| Controle | Dono da mudança | Efeito |
|---|---|---|
| Projeto | `VilmTkApp._on_project_selected` → `ProjectContext.activate` | ativa a pasta, avisa as abas pelo barramento de eventos |
| Perfil | `VilmTkApp.set_active_profile` → `profiles_api.set_active_profile` | grava `active` em `profiles.json` e `profile` em `settings.json` juntos |
| Idioma | `VilmTkApp.apply_language` | um único funil: valida, `i18n.set_language`, grava `ui_language`, `i18n.retranslate(root)`, status |

## Responsabilidade de cada aba e módulo dono

| Aba | Módulo dono | O que faz |
|---|---|---|
| Projetos | `tk_projects_tab` (`ProjectsTab`) | descobre pastas com `projeto.json`/mídia/letra, cria projeto, mostra estado e avisos |
| Produção | `tk_production_tab` (`ProductionTab`) | mídia cantada, playback, faixa na onda, letra, perfil, Gerar SRT, Gerar vídeo, lote, QC, `history.csv`, prévia fixa |
| Legendas | `tk_cue_editor` (`CueEditorTab`) | CRUD de cues com início/fim/texto, undo/redo, validação, re-síntese do ASS; hospeda `PipelineTab(tools_only)` |
| Cena | `tk_scene_tab` (`SceneTab`) | canvas com camadas de imagem/texto, lista, propriedades, timeline, snap/grade, área segura, efeitos e prévia real |
| Áudio | `tk_audio_tab` (`AudioTab`) | Analisar, Extrair instrumental, Voz +/−, Reduzir ruído, Normalizar/melhorar; campos editáveis, progresso e cancelar |
| Configurações | `tk_settings_tab` (`SettingsTab`) | ajustes de alinhamento/estilo/fundo/saída e CRUD completo de perfis (Novo, Duplicar, Salvar ajustes aqui, Excluir), Salvar/Restaurar preferências, painel "o que este perfil muda" |
| Assistente | `tk_assistant_tab` (`AssistantTab`) | refrão, ortografia e tradução via `opencode_bridge` |

Módulos de apoio da casca: `i18n` (tradução), `settings` (preferências),
`profiles_api` (perfis), `project_context` (contexto de projeto),
`process_runner` (execução de processos), `tk_tooltip` e `tk_video_preview`.
`desktop.py` permanece no pacote como casca/compatibilidade; as telas novas
não dependem dele para abrir a interface.

## Dados e serviços

- `settings.py` → `settings.json` em `%LOCALAPPDATA%\LyricsAligner` (com
  migração única de `desktop-preferences.json`, `.vilm_tkinter_config.json`,
  `webapp-config.json` e `config.json`).
- `profiles_api.py` → `profiles.json` na raiz da instalação.
- `project.py` + `project_context.py` → `<pasta>\projeto.json`, mesclado de
  forma aditiva; `letra.txt`, `FUNDO*`, `PLAYBACK*`, `saida\` e `trabalho\`
  não são apagados nem renomeados.
- `services/align.py` → geração de `saida\lyrics.srt`, JSON de debug e QC.
- `karaoke_jobs.py` → `make` (fundo, ASS queimado, intro, logo, transição,
  playback) e `trabalho\qc.json`.
- `karaoke.py`, `scene.py`, `imagefx.py`, `audiofx.py` → ASS `\kf`, cena,
  efeitos de imagem e processamento de áudio.
- `process_runner.py` → `run_job`/`run_streaming_process` com progresso,
  cancelamento e escolha correta de `pythonw`/`python`.

## Regras de tempo padronizadas

| Parâmetro | Valor real |
|---|---|
| `lead_ms` | 600 ms |
| `end_pad_ms` | 500 ms |
| Intervalo acústico curto (< 1100 ms) | 40% fica como permanência final do verso anterior, 60% vira início antecipado do próximo |
| `max_chars` | 30 no perfil `festa-ibuc`; 50 no `festa-genesis` |
| Duração | mín. 500 ms, máx. 10000 ms |
| `min_gap_ms` | 80 ms |
| Versos em slide | até 6 s ou quebra por linha em branco (estrofe) |
| Preenchimento | `\kf` (suave) |

As regras são implementadas em `timing_core.polish_singing_cue_times` e
alimentadas por `profiles_api.profile_settings` / `karaoke_jobs.run_project`.

## Idioma: um funil único

`i18n.LANGUAGES` tem **492 chaves em pt, en e ko**, com o mesmo conjunto de
chaves nos três idiomas. `i18n.t()` é o único ponto de leitura;
`VilmTkApp.apply_language` é o único ponto de escrita da preferência e o único
que dispara `retranslate(root)`. Nenhuma aba escreve `ui_language` diretamente.
O seletor do cabeçalho troca as sete abas na hora, sem perder projeto ou perfil.

## Saídas e QC

```
<pasta>\saida\<nome>.mp4
<pasta>\saida\<nome> - playback.mp4       (quando existe PLAYBACK*)
<pasta>\saida\lyrics.srt
<pasta>\history.csv
<pasta>\trabalho\qc.json
<pasta>\trabalho\scene-autosave.json      (autosave por projeto)
```

O QC **AVISA, NÃO BLOQUEIA**: avisos de canto sem legenda, pausa instrumental
longa e problemas de cena são gravados em `qc.json`, exibidos no painel e
registrados no `history.csv`; a renderização segue em frente.

## Limitações honestas

1. A prévia da aba **Produção** desenha frases fixas de exemplo; ela não usa
   as cues do projeto. A prévia da aba **Cena** usa `scene.render_preview` e
   **mostra o texto** das camadas (além das imagens).
2. O autosave da cena é **por projeto** quando o projeto tem `projeto.json`;
   o arquivo global `%LOCALAPPDATA%\LyricsAligner\scene-autosave.json` só é
   usado como fallback legado, sem projeto associado.
3. Não existe suíte de testes automáticos; a validação registrada é de
   sintaxe/importação e conferência manual com arquivos reais.
4. A instalação mantém uma cópia antiga em `python\Lib\site-packages`;
   sem `PYTHONPATH=app`, o comportamento pode ser o da versão legada.
5. `webapp.py` removido do pacote e do repositório em 27/11/2026; código
   arquivado em `_legado` na instalação local; `VilmPipeline.exe` permanece
   como legado fora do fluxo.
