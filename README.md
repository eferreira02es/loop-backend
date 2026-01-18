# LooP Backend

Backend do aplicativo LooP para gerenciamento de playlist Spotify.

## Deploy no Render

### Passo 1: Criar repositório no GitHub

1. Vá em https://github.com/new
2. Nome: `loop-backend`
3. Deixe público
4. Clique em "Create repository"

### Passo 2: Subir os arquivos

No terminal, dentro da pasta `backend`:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/loop-backend.git
git push -u origin main
```

### Passo 3: Criar serviço no Render

1. Acesse https://render.com
2. Clique em "New +" → "Blueprint"
3. Conecte seu GitHub
4. Selecione o repositório `loop-backend`
5. O Render vai ler o `render.yaml` e criar tudo automaticamente
6. Aguarde o deploy (2-5 minutos)

### Passo 4: Copiar a URL

Após o deploy, você terá uma URL tipo:
```
https://loop-app.onrender.com
```

Use essa URL no app Flutter!

## Endpoints da API

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/` | GET | Interface web para gerenciar playlist |
| `/api/current_link` | GET | Retorna o link atual (usado pelo Flutter) |
| `/api/heartbeat` | POST | Registra dispositivo online |
| `/api/devices_count` | GET | Retorna quantos dispositivos estão online |

## Estrutura

```
backend/
├── app.py              # Aplicação principal Flask
├── requirements.txt    # Dependências Python
├── render.yaml         # Configuração do Render
└── templates/
    └── index.html      # Interface web
```
