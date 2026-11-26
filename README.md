# Karaoke Pipeline

Pipeline para **produção de vídeos de karaokê** (letra sincronizada, ASS/SRT, burn com FFmpeg), organizado a partir de fluxos de trabalho usados em produção.

## Origem / Atribuição

Este projeto foi **extraído/adaptado** a partir de fluxos e ferramentas relacionados ao **Vilm Lyrics Aligner**.

- **Repositório upstream:** https://github.com/banjuman/vilm-lyrics-aligner
- **Site/produto:** https://voiceandfilm.com/en/product/software/vilm-lyrics-aligner/

Atribuição: Vilm Lyrics Aligner por banjuman/voiceandfilm (upstream MIT). Este repositório **não** é o projeto oficial Vilm Lyrics Aligner; é um pipeline de produção de karaokê baseado/derivado de ferramentas e fluxos relacionados.

## O que este repo inclui

- `scripts/gerar_video_padrao_santo.py` — geração de ASS/SRT/unidades e burn no padrão Santo (rolagem, 5 linhas, estilo configurável).
- `app/lyrics_aligner/` — módulos evoluídos do pipeline: perfis, alinhamento com regra 40/60, ASS com `\kf`, composição visual (`scene.json`), efeitos de imagem e áudio, CLI `make`/`audio`, painel Tk com QC e lote.
- `profiles.json` — perfis pre-set (`festa-ibuc`, `festa-genesis`).
- `docs/` — notas operacionais, patches do runtime Vilm 1.3.0, manual e guia de instalação dos módulos.
- `templates/` — exemplos de configuração/tempos (sem mídia).

## Módulos do pipeline (P0–P3)

- **P0 — perfis + QC:** perfis aplicados por completo, preflight que detecta canto sem legenda e pausas instrumentais, ASS com preenchimento suave e rolagem com a linha atual no topo.
- **P1 — editor:** Snap/grid, atalhos, autosave, histórico por hino, nomenclatura configurável e lote no painel Tk.
- **P2 — formato:** presets de resolução (inclusive vertical), área segura e transições `xfade` entre intro/karaokê/logo.
- **P3 — áudio:** separação Demucs (instrumental/voz), ganho independente da voz, redução de ruído e loudness EBU R128.

Veja [`docs/INSTALACAO_E_MODULOS.md`](docs/INSTALACAO_E_MODULOS.md) para aplicar os módulos a uma instalação do Vilm Pipeline 1.3 e [`docs/LEIA-ME-Vilm.txt`](docs/LEIA-ME-Vilm.txt) para o manual completo.

## O que este repo NÃO inclui

- Mídia (áudio/vídeo/imagens), saídas renderizadas, backups e arquivos temporários.
- Modelos ML, runtimes empacotados ou binários de terceiros.

## Requisitos

- Python 3.10+
- FFmpeg/FFprobe disponíveis no PATH (ou configurados pelo ambiente)
- Dependências do ecossistema de alinhamento/burn conforme o ambiente (ex.: Vilm Pipeline / lyrics-aligner)

## Uso básico

```bash
python scripts/gerar_video_padrao_santo.py --projeto "CAMINHO_DO_PROJETO" --tempos "CAMINHO/tempos.json" --backup-paginas --burn --verify
```

Consulte `--help` para parâmetros de fonte, tamanho, margens, pausa, fundo e mídia.

## Licença

MIT (ver `LICENSE`). A atribuição ao upstream Vilm Lyrics Aligner (MIT) deve ser preservada.
