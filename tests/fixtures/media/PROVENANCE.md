# Procedência das fixtures de mídia

O dataset só traz marcadores de mídia (`[imagem] ...`, `[audio] ...`), sem arquivo.
Estas fixtures existem para exercitar os ramos resolvidos do pipeline (D-037) com o
modelo real, via `scripts/probe_media.py`, e com duplos nos testes offline.

| Arquivo | Origem | Licença | Observação |
|---|---|---|---|
| `veiculo_nitido.jpg` | Wikimedia Commons, `File:Volkswagen Gol Hatchback.JPG`, miniatura de 500 px | Domínio público | foto clara de veículo |
| `veiculo_ruim.jpg` | derivada de `veiculo_nitido.jpg` | Domínio público (derivada) | reduzida a 40 px e ampliada, desfoque gaussiano 3, brilho 30%, JPEG qualidade 30 |
| `nao_veiculo.jpg` | Wikimedia Commons, `File:Brown tabby cat 2018 G1.jpg`, miniatura de 500 px | Domínio público | não é veículo |
| `audio_curto.wav` | gerado localmente com a voz sintética Microsoft Maria (pt-BR), 16 kHz mono | sem pessoa real | texto: "Tenho trinta e dois anos, e o meu carro é um Onix, ano dois mil e vinte." |

Nenhuma fixture contém dado pessoal. A derivação da foto ruim usou Pillow num uso
avulso (`uv run --with pillow`), sem entrar nas dependências do projeto.
