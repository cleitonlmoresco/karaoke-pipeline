# Karaoke Pipeline

Pipeline para **produção de vídeos de karaokê** (letra sincronizada, ASS/SRT, burn com FFmpeg), organizado a partir de fluxos de trabalho usados em produção.

## Origem / Atribuição

Este projeto foi **extraído/adaptado** a partir de fluxos e ferramentas relacionados ao **Vilm Lyrics Aligner**.

- **Repositório upstream:** https://github.com/banjuman/vilm-lyrics-aligner
- **Site/produto:** https://voiceandfilm.com/en/product/software/vilm-lyrics-aligner/

Atribuição: Vilm Lyrics Aligner por banjuman/voiceandfilm (upstream MIT). Este repositório **não** é o projeto oficial Vilm Lyrics Aligner; é um pipeline de produção de karaokê baseado/derivado de ferramentas e fluxos relacionados.

## O que este repo inclui

- `scripts/gerar_video_padrao_santo.py` — geração de ASS/SRT/unidades e burn no padrão Santo (rolagem, 5 linhas, estilo configurável).
- `docs/` — notas operacionais e patches aplicados no runtime Vilm 1.3.0.
- `templates/` — exemplos de configuração/tempos (sem mídia).

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
