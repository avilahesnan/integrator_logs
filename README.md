# Integrator Logs — API e painel de farmácias

API que recebe as requisições das farmácias integradas e alimenta o painel
administrativo ([logs_view](https://github.com/avilahesnan/logs_view)).

Django 5.2 + DRF + SQLite, publicado no Render.

---

## Como as peças se encaixam

```
Integrador da farmácia  --POST /api/log-sync/-->  SQLite (LogSync)
                                                     |
                           cadastro (Pharmacy) <-----+
                                   |                 |
                                   v                 v
                         Google Sheets          Painel (logs_view)
                      Cadastros + Monitoramento
```

* **`LogSync`** — uma linha por requisição recebida. É a prova de que a
  farmácia se comunicou. O endpoint não mudou de comportamento.
* **`Pharmacy`** — o cadastro, com `store_id` único. Existir aqui **não**
  significa estar ativa: atividade é medida só por `LogSync`.
* **`SyncState`** — controla para que cada tarefa diária rode uma vez só.

O monitoramento (última sync, última requisição, requisições do dia) é
**derivado por agregação**. Nada é duplicado nem recalculado e salvo.

---

## Persistência: o que você precisa saber

O disco do plano gratuito do Render é **efêmero**. A cada deploy ou reinício,
`db.sqlite3` volta ao estado versionado no git e os registros de monitoramento
do período são perdidos.

Por isso:

* o **Google Sheets é a fonte de verdade dos cadastros** — eles são
  reimportados automaticamente quando a tabela de farmácias aparece vazia,
  que é o sinal de container novo;
* os **registros de requisições** são aceitos como descartáveis, e o
  consolidado do dia é enviado ao Sheets antes disso acontecer.

O arquivo `db.sqlite3` continua versionado, como já era, e é ele que leva o
**esquema** (as migrações aplicadas) para o Render.

---

## Variáveis de ambiente no Render

Em **Dashboard → seu serviço → Environment → Environment Variables**.

### Obrigatórias

| Variável | Para que serve |
|---|---|
| `SECRET_KEY` | Chave do Django. Já existente. |
| `ADMIN_API_TOKEN` | Protege as rotas de escrita (cadastro, edição, sincronização manual). Gere com `python -c "import secrets; print(secrets.token_urlsafe(32))"`. **Sem ela, e com `DEBUG=False`, essas rotas respondem 503.** As rotas de leitura e o `/api/log-sync/` que as farmácias usam seguem abertos. |
| `GOOGLE_SHEETS_ID` | Id da planilha: o trecho entre `/d/` e `/edit` na URL. |
| Credenciais da conta de serviço | **Uma** das duas formas: um **Secret File** chamado `google-credentials.json` (recomendado, veja abaixo) **ou** a variável `GOOGLE_SERVICE_ACCOUNT_JSON` com o JSON inteiro. |

### Opcionais

| Variável | Padrão | Para que serve |
|---|---|---|
| `DEBUG` | `False` | Nunca ligar em produção. |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | `/etc/secrets/google-credentials.json` | Caminho do arquivo de credenciais. Só precisa mexer se o Secret File tiver outro nome. |
| `SHEETS_CADASTROS_TAB` | `Cadastros` | Nome da aba dos cadastros. Criada sozinha se não existir. |
| `SHEETS_MONITORAMENTO_TAB` | `Monitoramento` | Nome da aba do consolidado. Criada sozinha se não existir. |
| `SHEETS_SYNC_ENABLED` | `True` | `False` desliga a integração inteira sem mexer no código. |
| `SHEETS_EXPORT_HOUR` | `23` | Hora local (0–23) a partir da qual o consolidado do dia é enviado. |
| `MONITOR_LATE_MINUTES` | `45` | Minutos sem requisição até a farmácia aparecer como *atrasada*. |
| `MONITOR_OFFLINE_MINUTES` | `1440` | Minutos sem requisição até aparecer como *sem comunicação*. |
| `CORS_ALLOWED_ORIGINS` | `https://logs-view.netlify.app,http://localhost:3000,http://127.0.0.1:3000` | Origens liberadas, separadas por vírgula. **Ajuste para o endereço real do painel.** |
| `CSRF_TRUSTED_ORIGINS` | o mesmo que o CORS | Raramente precisa mexer. |

> O painel envia o token no cabeçalho `X-Admin-Token`, que já está liberado no
> CORS. Se o painel não estiver no domínio padrão acima, **é obrigatório**
> ajustar `CORS_ALLOWED_ORIGINS` — senão o navegador bloqueia cadastro e edição.

---

## Configurando o Google Sheets (uma vez)

1. Em [console.cloud.google.com](https://console.cloud.google.com), crie um
   projeto (ou use um existente).
2. **APIs e serviços → Biblioteca** → procure **Google Sheets API** → **Ativar**.
3. **APIs e serviços → Credenciais → Criar credenciais → Conta de serviço**.
   Dê um nome e confirme. Não precisa conceder papéis.
4. Abra a conta de serviço criada → aba **Chaves** → **Adicionar chave → Criar
   nova chave → JSON**. O arquivo baixa automaticamente.
5. Entregue esse JSON ao backend de **uma** das duas formas:

   **(a) Secret File — recomendado no Render.** Em **Environment → Secret
   Files**, suba o arquivo com o nome `google-credentials.json`. O Render o
   monta em `/etc/secrets/google-credentials.json`, que é exatamente onde o
   código procura por padrão: não precisa declarar variável nenhuma. Para usar
   outro nome, aponte o caminho completo em `GOOGLE_SERVICE_ACCOUNT_FILE`.
   Secret Files são a opção mais segura das duas — não aparecem na aba de
   variáveis de ambiente nem num dump de ambiente que caia nos logs.

   **(b) JSON inline.** Cole o conteúdo inteiro do arquivo em
   `GOOGLE_SERVICE_ACCOUNT_JSON`. Se as duas existirem, esta vence, o que
   serve para sobrepor o arquivo sem removê-lo.

   Nas duas formas o `\n` escapado da chave privada é tratado.
6. Crie a planilha no Google Sheets e **compartilhe com o `client_email` que
   está dentro do JSON**, com permissão de **Editor**. Esse é o passo que mais
   costuma ser esquecido: sem ele a API responde 403.
7. Copie o id da planilha da URL para `GOOGLE_SHEETS_ID`:
   `docs.google.com/spreadsheets/d/`**`ESTE_PEDACO`**`/edit`.

As duas abas são criadas com cabeçalho na primeira sincronização. Colunas que
você acrescentar à mão nelas são preservadas: a gravação só reescreve as
colunas que o painel conhece.

Para conferir se ficou tudo certo:

```bash
curl -s https://integrator-logs.onrender.com/api/sheets/status/ | python3 -m json.tool
```

`enabled: true` com `configuration_error: null` significa credencial carregada
e planilha alcançável. O campo `credentials_source` diz de onde a credencial
saiu — o caminho do arquivo ou o nome da variável —, que é a forma rápida de
descobrir se o Secret File foi encontrado. A resposta também mostra quando cada
tarefa rodou pela última vez.

---

## Quando a sincronização acontece

O plano gratuito do Render não tem Cron Job, e não foi introduzido nenhum
serviço pago. Como as farmácias batem no endpoint a cada ~20 minutos, cada
requisição recebida serve de gatilho barato:

* **Importação dos cadastros** — uma vez por dia, e também assim que a tabela
  de farmácias aparece vazia (container novo).
* **Exportação do consolidado** — uma vez por dia, a partir de
  `SHEETS_EXPORT_HOUR`.

As duas rodam em *thread* separada: não atrasam a resposta do endpoint, e
qualquer falha é registrada sem derrubar nada. Se uma falhar, a trava do dia é
liberada e a próxima requisição tenta de novo.

O painel também tem o menu **Sheets** para disparar as duas manualmente.

---

## Endpoints

### Usados pelas farmácias (inalterados)

| Método | Rota | Observação |
|---|---|---|
| `POST` | `/api/log-sync/` | Mesmo payload de sempre. Aceita agora um campo **opcional** `status` (`success`/`error`); quem não envia continua sendo tratado como sucesso. |

### Leitura (abertos)

| Método | Rota | Devolve |
|---|---|---|
| `GET` | `/api/last-logs/` | Última requisição de cada loja. Mantido para compatibilidade. |
| `GET` | `/api/logs/<store_id>/` | Histórico de requisições da farmácia. |
| `GET` | `/api/pharmacies/` | Tela principal: cadastro + atividade real de cada farmácia. |
| `GET` | `/api/pharmacies/<store_id>/` | Cadastro de uma farmácia. |
| `GET` | `/api/sheets/status/` | Estado da integração e das tarefas. |

### Escrita (exigem `X-Admin-Token`)

| Método | Rota | Faz |
|---|---|---|
| `POST` | `/api/pharmacies/create/` | Cadastra e espelha no Sheets. |
| `PUT`/`PATCH` | `/api/pharmacies/<store_id>/update/` | Edita e atualiza o Sheets. |
| `POST` | `/api/pharmacies/<store_id>/retry-sheets/` | Reenvia um cadastro pendente. |
| `POST` | `/api/sheets/retry-pending/` | Reenvia todos os pendentes. |
| `POST` | `/api/sheets/import/` | Importa cadastros do Sheets agora. |
| `POST` | `/api/sheets/export/` | Exporta o consolidado agora. |

**Uma falha no Google Sheets nunca desfaz o cadastro local.** O registro é
gravado, marcado como pendente com o motivo do erro, e pode ser reenviado pelo
painel.

---

## Rodando localmente

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # preencha SECRET_KEY e, se for testar, o Sheets
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Testes:

```bash
.venv/bin/python manage.py test logs
```

---

## Deploy

O `Procfile` roda as migrações antes de subir o gunicorn:

```
web: python manage.py migrate --noinput && gunicorn core.wsgi
```

> Se o seu serviço no Render usa um **Start Command** definido pelo painel em
> vez do `Procfile`, atualize-o para o mesmo comando acima — caso contrário a
> migração nova não é aplicada no deploy.
