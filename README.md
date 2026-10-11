# AMD AI League — Match 3 (RAG) — envio até 13/10/2026

Container que indexa uma pasta de documentos mistos e responde perguntas com o
**valor exato** e a **lista exata de arquivos-fonte**. Regras completas em `CONTRACT.md`.

## Como funciona

```
container start ─► server.py (resident): carrega LLM + VLM na GPU, escuta /tmp/mc3.sock
--index  ─► app.py ─► server: walk defensivo ─► parsers (pdf/docx/xlsx/csv/txt/log/py)
                                  ─► VLM transcreve png/jpg ─► chunks.json em /app/index
--query  ─► app.py (processo novo, fino) ─► server: BM25 + boost de identificadores
                                  ─► 2º salto por identificador raro (ticket, código)
                                  ─► LLM extrai {answer, used} ─► citações = arquivos dos trechos usados
                                  ─► /app/output/<query-id>_output.json (escrita atômica)
```

Decisões que protegem a pontuação:

- **Refusal por padrão**: `app.py` grava `{"answer":"","citations":[]}` antes de perguntar ao servidor. Se algo falhar, a saída é um recuso válido, nunca um JSON malformado.
- **Citações por necessidade**: o LLM devolve só os números dos trechos sem os quais não responderia; os arquivos vêm desses trechos. Nunca despejamos a recuperação inteira.
- **Documentos WITHDRAWN/SUPERSEDED** perdem peso na recuperação e o prompt manda ignorá-los quando há revisão mais nova.
- **Multi-hop**: identificadores que aparecem em até 3 arquivos (ORR-1847, E7731) disparam uma segunda busca; produto (TQ-40) não dispara.
- **Walk defensivo**: erro por arquivo é registrado em `manifest.json` e a indexação continua (vazio, sem permissão, criptografado, tipo desconhecido).
- **GPU obrigatória**: o backend `hf` aborta se não houver GPU, em vez de cair para CPU.

Modelos (cabem nos 30 GiB disponíveis): `Qwen/Qwen2.5-7B-Instruct` (texto) e
`Qwen/Qwen2-VL-2B-Instruct` (imagens).

## Validação local sem GPU (já feita)

```
python3 eval.py --backend stub
SCORE 80/200
```

O backend `stub` não tem modelo: acerta as 4 perguntas que a recuperação sozinha resolve e
erra exatamente as que dependem de LLM/VLM (extração de valor, imagens, multi-hop, recuso).
Serve para provar o contrato: índice persistido, processo novo por pergunta, socket,
escrita atômica, walk com arquivos quebrados. O resultado real vem da etapa abaixo.

## Passo a passo no ambiente da AMD (fazer até 12/10)

1. Entrar em https://notebooks.amd.com/hackathon com o login do AMD Developer Program
   (3 h de GPU por dia; o tempo ocioso conta).
2. Clonar este repositório e entrar em `hackathons/amd_league_rag/mc3`.
3. Baixar os pesos (uma vez, com rede):

   ```bash
   pip install -U "huggingface_hub[cli]"
   hf download Qwen/Qwen2.5-7B-Instruct --local-dir models/llm
   hf download Qwen/Qwen2-VL-2B-Instruct --local-dir models/vlm
   ```

4. Teste rápido sem Docker, direto no notebook (GPU visível):

   ```bash
   pip install --no-deps -r app/requirements.txt
   MC3_LLM=$PWD/models/llm MC3_VLM=$PWD/models/vlm python3 eval.py --backend hf
   ```

   Meta: 200/200 nas 10 perguntas de amostra.
5. Construir, validar e publicar com um comando (precisa de Docker e de um token do
   GitHub com `write:packages`, nunca comitado):

   ```bash
   export GHCR_USER=tatianamartins-vyson
   export GHCR_TOKEN=...   # cole só no terminal
   bash build_and_push.sh
   ```

   O script faz build na base ROCm, confere o torch, mede o tamanho, roda `selfcheck.py`
   e publica `ghcr.io/tatianamartins-vyson/mc3-rag:v1`.

6. Tornar o pacote público no GitHub (Packages → mc3-rag → Package settings → Change
   visibility) e colar o endereço no campo "Mini Desafio 3 Imagem" do lablab antes do
   fim do dia 13/10.

## Se o tempo apertar

Prioridade: (1) LLM + parsers rodando na GPU, (2) VLM nas imagens, (3) ajuste de prompt
para citações. Mesmo sem o VLM, 8 das 10 categorias são de texto.
