import os
import math
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime # Importando modulo inteiro para evitar conflitos
import decimal
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_cors import CORS
import threading
import time
import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import pandas as pd
import logging

# Configuração de logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- CONFIGURAÇÃO ---
app = Flask(__name__, template_folder='templates')
CORS(app)  # Permite requisições do Flutter

# Configuração do banco de dados
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://localhost/loop_playlist')

# Configuração de Threads
# Garante que apenas uma thread de automação rode por vez (mesmo com workers)
automation_lock = threading.Lock()

# Configuração Spotify
SPOTIPY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID", "24ec1e4013d948ba87c2e85d623521d2")
SPOTIPY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET", "4d57f99be4834ed682684e607aeb3337")

try:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET
    ))
except Exception as e:
    print(f"Erro ao configurar Spotify: {e}")
    sp = None

# Variável global para o link atual (atualizada pelo motor)
current_link_data = {
    "link": "",
    "duracao_min": 3.0,
    "nome": "",
    "timestamp": 0
}

# Configurações globais
DEVICE_TIMEOUT_SECONDS = 300 # 5 minutos

config = {
    "quantidade_aparelhos": 200,
    "reset_automatico": 1
}

# --- FUNÇÕES AUXILIARES ---
def get_id_from_url(url):
    """Extrai apenas o ID do link do Spotify"""
    try:
        return url.split("/")[-1].split("?")[0].strip()
    except:
        return ""

# --- FUNÇÕES DO BANCO DE DADOS ---
def get_db_connection():
    """Cria conexão com o PostgreSQL"""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    """Inicializa as tabelas do banco de dados"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Tabela de músicas/playlist (Versão Base)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS playlist (
            id SERIAL PRIMARY KEY,
            link_musica TEXT NOT NULL,
            nome_musica TEXT NOT NULL,
            plays_desejados INTEGER DEFAULT 0,
            plays_atuais INTEGER DEFAULT 0,
            plays_mensais INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Pendente',
            duracao_min REAL DEFAULT 3.0,
            data_adicao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Migração: Adicionar novos campos na tabela playlist
    new_columns = [
        ("track_id", "TEXT"),
        ("playlist_id", "TEXT"),
        ("plays_hoje", "INTEGER DEFAULT 0"),
        ("data_ultimo_play", "DATE DEFAULT CURRENT_DATE")
    ]
    
    for col_name, col_type in new_columns:
        try:
            cur.execute(f'ALTER TABLE playlist ADD COLUMN IF NOT EXISTS {col_name} {col_type}')
        except Exception as e:
            # Fallback para versões antigas do Postgres
            conn.rollback()
            try:
                cur.execute(f'ALTER TABLE playlist ADD COLUMN {col_name} {col_type}')
            except:
                conn.rollback()
    
    # Nova Tabela: playlists (para gerenciamento)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS playlists (
            id SERIAL PRIMARY KEY,
            url TEXT NOT NULL,
            nome TEXT,
            data_adicao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Nova Tabela: musicas_controle (meta mensal)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS musicas_controle (
            id SERIAL PRIMARY KEY,
            track_id TEXT UNIQUE NOT NULL,
            nome TEXT,
            meta_mensal INTEGER DEFAULT 0,
            plays_diarios INTEGER DEFAULT 0,
            mes_atual TEXT,
            plays_mes_atual INTEGER DEFAULT 0
        )
    ''')
    
    # Tabela de configuração
    cur.execute('''
        CREATE TABLE IF NOT EXISTS config (
            id SERIAL PRIMARY KEY,
            chave TEXT UNIQUE NOT NULL,
            valor TEXT NOT NULL
        )
    ''')
    
    # Tabela de dispositivos conectados (heartbeat)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS devices (
            id SERIAL PRIMARY KEY,
            device_id TEXT UNIQUE NOT NULL,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Nova Tabela: plays_diarios (histórico de plays por dia)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS plays_diarios (
            id SERIAL PRIMARY KEY,
            track_id TEXT NOT NULL,
            data DATE NOT NULL,
            plays INTEGER DEFAULT 0,
            UNIQUE(track_id, data)
        )
    ''')
    
    # Inserir configuração padrão se não existir
    default_configs = [
        ('quantidade_aparelhos', '200'),
        ('reset_automatico', '1') # 1 = Sim, 0 = Não
    ]
    
    for chave, valor in default_configs:
        cur.execute('''
            INSERT INTO config (chave, valor) VALUES (%s, %s)
            ON CONFLICT (chave) DO NOTHING
        ''', (chave, valor))
    
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Banco de dados inicializado com sucesso!")

def carregar_playlist():
    """Carrega todas as músicas do banco"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM playlist ORDER BY id')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in rows]

def salvar_musica(link, nome, plays_desejados, duracao_min):
    """Adiciona uma nova música"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO playlist (link_musica, nome_musica, plays_desejados, duracao_min, status)
        VALUES (%s, %s, %s, %s, 'Pendente')
    ''', (link, nome, plays_desejados, duracao_min))
    conn.commit()
    cur.close()
    conn.close()

def atualizar_musica(id, plays_atuais, plays_mensais, status):
    """Atualiza uma música existente"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        UPDATE playlist SET plays_atuais = %s, plays_mensais = %s, status = %s
        WHERE id = %s
    ''', (plays_atuais, plays_mensais, status, id))
    conn.commit()
    cur.close()
    conn.close()

def deletar_musica(id):
    """Remove uma música"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM playlist WHERE id = %s', (id,))
    conn.commit()
    cur.close()
    conn.close()

def carregar_config():
    """Carrega configurações do banco"""
    global config
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT chave, valor FROM config')
        rows = cur.fetchall()
        for row in rows:
            config[row['chave']] = int(row['valor']) if row['valor'].isdigit() else row['valor']
        cur.close()
        conn.close()
    except:
        pass

def salvar_config_db(chave, valor):
    """Salva uma configuração no banco"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO config (chave, valor) VALUES (%s, %s)
        ON CONFLICT (chave) DO UPDATE SET valor = %s
    ''', (chave, str(valor), str(valor)))
    conn.commit()
    cur.close()
    conn.close()

def registrar_heartbeat(device_id):
    """Registra que um dispositivo está ativo"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO devices (device_id, last_seen) VALUES (%s, CURRENT_TIMESTAMP)
        ON CONFLICT (device_id) DO UPDATE SET last_seen = CURRENT_TIMESTAMP
    ''', (device_id,))
    conn.commit()
    cur.close()
    conn.close()

def contar_dispositivos_ativos():
    """Conta dispositivos que fizeram heartbeat nos últimos 5 minutos"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f'''
        SELECT COUNT(*) as count FROM devices 
        WHERE last_seen > NOW() - INTERVAL '{DEVICE_TIMEOUT_SECONDS} seconds'
    ''')
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result['count'] if result else 0

def get_playlists_db():
    """Retorna todas as playlists cadastradas"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM playlists ORDER BY id')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in rows]

def adicionar_playlist_db(url, nome):
    """Adiciona uma nova playlist"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO playlists (url, nome) VALUES (%s, %s)', (url, nome))
    conn.commit()
    cur.close()
    conn.close()

def remover_playlist_db(id):
    """Remove uma playlist pelo ID"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM playlists WHERE id = %s', (id,))
    conn.commit()
    cur.close()
    conn.close()

# --- LÓGICA DE NEGÓCIO ---

def validar_musica_playlists(link_musica, plays_desejados_dia, meta_mensal, duracao_manual=None):
    """
    Valida em quais playlists a música está e prepara as entradas.
    Retorna lista de músicas a adicionar e informações de status.
    """
    if not sp:
        return {"error": "Spotify não configurado"}
        
    track_id = get_id_from_url(link_musica)
    if not track_id:
        return {"error": "Link inválido"}
        
    # Busca informações da música
    try:
        track_info = sp.track(track_id)
        nome_musica = track_info['name']
        
        # Se usuário definiu duração manual, usa ela
        if duracao_manual and float(duracao_manual) > 0.0:
            duracao_min = float(duracao_manual)
            print(f"⏱️ Usando duração manual: {duracao_min} min")
        else:
            duracao_min = round(track_info['duration_ms'] / 60000, 1)
            
        # Artista - Nome
        artistas = ", ".join([artist['name'] for artist in track_info['artists']])
        nome_completo = f"{artistas} - {nome_musica}"
        print(f"🎵 Validando: {nome_completo}")
    except Exception as e:
        return {"error": f"Erro ao buscar música no Spotify: {e}"}

    # Busca todas as playlists cadastradas
    playlists = get_playlists_db()
    
    encontrados = []
    
    # Verifica em cada playlist
    for pl in playlists:
        try:
            pl_id = get_id_from_url(pl['url'])
            # Otimização: buscar se a track está na playlist
            # Para playlists grandes isso pode ser lento, mas vamos iterar
            # Melhor seria usar o endpoint que verifica (se existir) ou iterar paginado
            
            # Vamos iterar (simples e robusto para < 100 tracks)
            offset = 0
            found = False
            while True:
                response = sp.playlist_tracks(pl_id, fields="items(track(id)),next", limit=100, offset=offset)
                for item in response['items']:
                    if item['track'] and item['track']['id'] == track_id:
                         # Link com contexto da playlist
                        link_contexto = f"https://open.spotify.com/track/{track_id}?context=spotify%3Aplaylist%3A{pl_id}"
                        encontrados.append({
                            "playlist_nome": pl['nome'],
                            "link": link_contexto,
                            "playlist_id": pl_id
                        })
                        found = True
                        break
                
                if found or not response['next']:
                    break
                offset += 100
                
        except Exception as e:
            print(f"Erro ao verificar playlist {pl['url']}: {e}")
            continue

    if not encontrados:
        return {"error": "Música não encontrada em nenhuma das playlists cadastradas."}
        
    # Divide os plays
    plays_por_playlist = max(1, plays_desejados_dia // len(encontrados))
    
    resultado = {
        "musica": {
            "track_id": track_id,
            "nome": nome_completo,
            "duracao": duracao_min,
            "meta_mensal": meta_mensal
        },
        "entradas": [],
        "playlists_encontradas": [e['playlist_nome'] for e in encontrados]
    }
    
    for item in encontrados:
        resultado["entradas"].append({
            "link_musica": item['link'],
            "nome_musica": f"{nome_completo} ({item['playlist_nome']})",
            "plays_desejados": plays_por_playlist,
            "duracao_min": duracao_min,
            "track_id": track_id,
            "playlist_id": item['playlist_id']
        })
        
    return resultado

def salvar_validacao(resultado):
    """Salva o resultado da validação no banco"""
    musica = resultado['musica']
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Salva/Atualiza o controle da música (meta mensal)
    cur.execute('''
        INSERT INTO musicas_controle (track_id, nome, meta_mensal, plays_diarios, mes_atual)
        VALUES (%s, %s, %s, %s, TO_CHAR(CURRENT_DATE, 'YYYY-MM'))
        ON CONFLICT (track_id) DO UPDATE SET 
            meta_mensal = %s,
            plays_diarios = %s,
            nome = %s
    ''', (musica['track_id'], musica['nome'], musica['meta_mensal'], 
          len(resultado['entradas']) * resultado['entradas'][0]['plays_desejados'], # Total diário estimado
          musica['meta_mensal'], 
          len(resultado['entradas']) * resultado['entradas'][0]['plays_desejados'],
          musica['nome']))
    
    # 2. Adiciona as entradas na fila de execução
    for entrada in resultado['entradas']:
        cur.execute('''
            INSERT INTO playlist (link_musica, nome_musica, plays_desejados, duracao_min, status, track_id, playlist_id)
            VALUES (%s, %s, %s, %s, 'Pendente', %s, %s)
        ''', (entrada['link_musica'], entrada['nome_musica'], entrada['plays_desejados'], 
              entrada['duracao_min'], entrada['track_id'], entrada['playlist_id']))
              
    conn.commit()
    cur.close()
    conn.close()


# --- MOTOR DE AUTOMAÇÃO ---

def executar_reset_diario():
    """Executa o reset diário dos plays"""
    print(f"[{time.strftime('%H:%M:%S')}] 🔄 Executando reset diário...")
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Resetar plays_hoje de todas as músicas
    cur.execute("UPDATE playlist SET plays_hoje = 0")
    
    # Reativar músicas que não atingiram a meta mensal
    # 1. Busca metas
    cur.execute("SELECT track_id, meta_mensal, plays_mes_atual FROM musicas_controle")
    metas = cur.fetchall()
    
    reativadas = 0
    for m in metas:
        if m['plays_mes_atual'] < m['meta_mensal']:
            # Reseta plays_atuais e status para Pendente
            cur.execute('''
                UPDATE playlist 
                SET plays_atuais = 0, status = 'Pendente' 
                WHERE track_id = %s
            ''', (m['track_id'],))
            reativadas += 1
            
    # Atualiza data do último reset
    hoje_str = datetime.datetime.now().strftime('%Y-%m-%d')
    cur.execute('''
        INSERT INTO config (chave, valor) VALUES ('last_reset_date', %s)
        ON CONFLICT (chave) DO UPDATE SET valor = %s
    ''', (hoje_str, hoje_str))
    
    conn.commit()
    cur.close()
    conn.close()
    print(f"✅ Reset concluído! {reativadas} músicas reativadas para o novo dia.")

def motor_automacao():
    """Loop principal que processa a playlist"""
    global current_link_data
    print(">>> Motor de automação iniciado. <<<")
    
    # Aguarda o banco estar pronto
    time.sleep(5)
    
    while True:
        try:
            carregar_config()
            
            # 1. VERIFICAÇÃO DE DISPOSITIVOS ONLINE
            dispositivos_online = contar_dispositivos_ativos()
            
            # Se não há dispositivos, pausa o sistema
            if dispositivos_online == 0:
                print(f"[{time.strftime('%H:%M:%S')}] 💤 Sem dispositivos online. Aguardando...")
                current_link_data = {
                    "link": "",
                    "duracao_min": 3.0,
                    "nome": "",
                    "timestamp": 0
                }
                time.sleep(30)
                continue
                
            # 2. PROCESSO DE RESET DIÁRIO (21h)
            try:
                # Usando namespace explicito datetime.datetime e datetime.timedelta
                agora = datetime.datetime.utcnow() - datetime.timedelta(hours=3) # Horário de Brasília
                hoje_str = agora.strftime('%Y-%m-%d')
                
                # Verifica se já resetou hoje
                last_reset = config.get('last_reset_date', '')
                
                # Reseta se for >= 21h e ainda não tiver resetado hoje
                if agora.hour >= 21 and last_reset != hoje_str and config.get('reset_automatico', 1) == 1:
                    executar_reset_diario()
                    carregar_config() # Recarrega config
            except Exception as e:
                print(f"Erro no reset diário: {e}")

            # 3. PROCESSAMENTO DA FILA
            playlist = carregar_playlist()
            
            # Encontra a primeira música pendente ou em execução
            musica_atual = None
            for m in playlist:
                if m['status'] in ['Em Execução', 'Pendente']:
                    musica_atual = m
                    break
            
            if musica_atual:
                musica_id = musica_atual['id']
                
                # Se está Pendente, marca como Em Execução
                if musica_atual['status'] == 'Pendente':
                    atualizar_musica(
                        musica_id,
                        musica_atual['plays_atuais'],
                        musica_atual['plays_mensais'],
                        'Em Execução'
                    )
                
                # Verifica se ainda precisa tocar mais vezes
                if musica_atual['plays_atuais'] < musica_atual['plays_desejados']:
                    duracao = musica_atual['duracao_min']
                    intervalo_ciclo_seg = (duracao * 60) + 10
                    
                    # Atualiza o link atual para os dispositivos
                    current_link_data = {
                        "link": musica_atual['link_musica'],
                        "duracao_min": duracao,
                        "nome": musica_atual['nome_musica'],
                        "timestamp": int(time.time() + musica_atual['plays_atuais']) # Garante timestamp único por play
                    }
                    
                    # Usa dispositivos online (já verificado > 0)
                    if dispositivos_online == 0: dispositivos_online = 1
                    
                    print(f"[{time.strftime('%H:%M:%S')}] Enviando '{musica_atual['nome_musica']}' | "
                          f"Dispositivos: {dispositivos_online} | "
                          f"Progresso: {musica_atual['plays_atuais'] + dispositivos_online}/{musica_atual['plays_desejados']}")
                    
                    # Calcula plays a somar baseado em dispositivos online
                    plays_a_somar = min(
                        dispositivos_online,
                        musica_atual['plays_desejados'] - musica_atual['plays_atuais']
                    )
                    
                    # Atualiza os plays no banco (Plays Atuais, Plays Mensais, Plays Hoje)
                    # Preciso atualizar plays_hoje e plays_mes_atual na tabela de controle também
                    conn = get_db_connection()
                    cur = conn.cursor()
                    
                    # Atualiza playlist
                    cur.execute('''
                        UPDATE playlist SET 
                            plays_atuais = plays_atuais + %s, 
                            plays_mensais = plays_mensais + %s,
                            plays_hoje = plays_hoje + %s,
                            data_ultimo_play = CURRENT_DATE,
                            status = 'Em Execução'
                        WHERE id = %s
                    ''', (plays_a_somar, plays_a_somar, plays_a_somar, musica_id))
                    
                    # Atualiza controle (Meta Mensal)
                    if musica_atual.get('track_id'):
                        cur.execute('''
                            UPDATE musicas_controle SET plays_mes_atual = plays_mes_atual + %s
                            WHERE track_id = %s
                        ''', (plays_a_somar, musica_atual['track_id']))
                        
                        # Registra histórico diário para gráficos
                        cur.execute('''
                            INSERT INTO plays_diarios (track_id, data, plays)
                            VALUES (%s, CURRENT_DATE, %s)
                            ON CONFLICT (track_id, data) 
                            DO UPDATE SET plays = plays_diarios.plays + %s
                        ''', (musica_atual['track_id'], plays_a_somar, plays_a_somar))
                        
                    conn.commit()
                    cur.close()
                    conn.close()
                    
                    # Aguarda o tempo do ciclo
                    time.sleep(intervalo_ciclo_seg)
                else:
                    # Concluiu todos os plays DO DIA/LOTE
                    atualizar_musica(
                        musica_id,
                        musica_atual['plays_atuais'],
                        musica_atual['plays_mensais'],
                        'Concluído'
                    )
                    time.sleep(1)
            else:
                # Não há músicas na fila
                # Verifica reset manual ou automático aqui também se necessário
                time.sleep(30)
                
        except Exception as e:
            print(f"ERRO NO MOTOR: {e}")
            time.sleep(15)

# --- API ENDPOINTS PARA O FLUTTER ---
@app.route('/api/current_link')
def api_current_link():
    """Retorna o link atual para os dispositivos Flutter - busca direto do banco"""
    device_id = request.args.get('device_id', 'unknown')
    
    # Registra heartbeat do dispositivo
    try:
        registrar_heartbeat(device_id)
    except:
        pass
    
    # Busca a música em execução diretamente do banco (resolve problema de workers)
    try:
        playlist = carregar_playlist()
        for m in playlist:
            if m['status'] == 'Em Execução':
                # Timestamp ESTÁVEL dentro de um ciclo:
                # - Usa ID * 10000 + plays_atuais
                # - Só muda quando o motor incrementa plays_atuais (a cada ciclo)
                # - NÃO usa time.time() para evitar mudanças a cada segundo
                unique_timestamp = (m['id'] * 10000) + m['plays_atuais']
                
                return jsonify({
                    "link": m['link_musica'],
                    "duracao_min": float(m['duracao_min']),
                    "nome": m['nome_musica'],
                    "timestamp": unique_timestamp
                })
    except Exception as e:
        print(f"Erro ao buscar link: {e}")
    
    # Se não tem música em execução, retorna vazio
    return jsonify({
        "link": "",
        "duracao_min": 3.0,
        "nome": "",
        "timestamp": 0
    })

@app.route('/api/heartbeat', methods=['POST'])
def api_heartbeat():
    """Registra que um dispositivo está ativo"""
    data = request.get_json() or {}
    device_id = data.get('device_id', 'unknown')
    
    try:
        registrar_heartbeat(device_id)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/devices_count')
def api_devices_count():
    """Retorna quantos dispositivos estão ativos"""
    count = contar_dispositivos_ativos()
    return jsonify({"count": count})

def serialize_data(data):
    """Converte objetos não serializáveis (datetime, Decimal) para string/float"""
    if isinstance(data, list):
        return [serialize_data(item) for item in data]
    elif isinstance(data, dict):
        return {key: serialize_data(value) for key, value in data.items()}
    elif isinstance(data, (datetime.datetime, datetime.date)):
        return data.isoformat()
    elif isinstance(data, decimal.Decimal):
        return float(data)
    return data

@app.route('/api/playlists', methods=['GET'])
def api_get_playlists():
    try:
        playlists = get_playlists_db()
        return jsonify(serialize_data(playlists))
    except Exception as e:
        print(f"Erro ao buscar playlists: {e}")
        return jsonify([]) # Retorna lista vazia em vez de 500

@app.route('/api/playlists', methods=['POST'])
def api_add_playlist():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"error": "URL obrigatória"}), 400
        
    nome = "Nova Playlist"
    if sp:
        try:
            pl_id = get_id_from_url(url)
            pl_data = sp.playlist(pl_id, fields="name")
            nome = pl_data['name']
        except:
            pass
            
    adicionar_playlist_db(url, nome)
    return jsonify({"status": "ok", "nome": nome})

@app.route('/api/playlists/<int:id>', methods=['DELETE'])
def api_remove_playlist(id):
    remover_playlist_db(id)
    return jsonify({"status": "ok"})

# --- SISTEMA DE JOBS EM BACKGROUND ---
jobs = {}

def processar_validacao_background(job_id, link, plays_diarios, meta_mensal, duracao_manual):
    """Executa a validação em thread separada"""
    try:
        jobs[job_id] = {"status": "processing", "message": "Validando músicas nas playlists..."}
        
        # Simula um pequeno delay para garantir que o status seja lido
        time.sleep(1)
        
        resultado = validar_musica_playlists(link, plays_diarios, meta_mensal, duracao_manual)
        
        if "error" in resultado:
            jobs[job_id] = {"status": "error", "message": resultado["error"]}
        else:
            salvar_validacao(resultado)
            jobs[job_id] = {
                "status": "completed", 
                "message": f"Sucesso! Encontrada em {len(resultado['playlists_encontradas'])} playlists.",
                "resultado": resultado
            }
    except Exception as e:
        jobs[job_id] = {"status": "error", "message": f"Erro interno: {str(e)}"}

@app.route('/api/add_music_smart', methods=['POST'])
def api_add_music_smart():
    data = request.json
    link = data.get('link')
    plays_diarios = int(data.get('plays_diarios', 100))
    meta_mensal = int(data.get('meta_mensal', 3000))
    duracao_manual = data.get('duracao_manual') # Opcional
    
    if not link:
        return jsonify({"error": "Link obrigatório"}), 400
        
    # Cria um ID para o job
    job_id = str(int(time.time() * 1000))
    jobs[job_id] = {"status": "queued", "message": "Iniciando validação..."}
    
    # Inicia thread
    thread = threading.Thread(target=processar_validacao_background, args=(job_id, link, plays_diarios, meta_mensal, duracao_manual))
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "ok", "job_id": job_id})

@app.route('/api/job_status/<job_id>')
def api_job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "error", "message": "Job não encontrado"}), 404
    return jsonify(job)

@app.route('/get_stats')
def get_stats():
    """Retorna estatísticas para a tela de controle"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Busca dados de controle
        cur.execute('SELECT * FROM musicas_controle')
        controles = cur.fetchall()
        
        stats = []
        for c in controles:
            track_id = c['track_id']
            
            # Calcula plays hoje (soma de todas as playlists dessa música)
            cur.execute('SELECT SUM(plays_hoje) as hoje FROM playlist WHERE track_id = %s', (track_id,))
            res = cur.fetchone()
            plays_hoje = res['hoje'] if res and res['hoje'] else 0
            
            status_meta = "Em Progresso"
            percentual = 0
            if c['meta_mensal'] > 0:
                percentual = (c['plays_mes_atual'] / c['meta_mensal']) * 100
                if percentual >= 100:
                    status_meta = "Meta Atingida!"
                elif percentual >= 90:
                    status_meta = "Perto da Meta"
            
            stats.append({
                "nome": c['nome'],
                "plays_hoje": plays_hoje,
                "plays_mes": c['plays_mes_atual'],
                "meta_mensal": c['meta_mensal'],
                "percentual": round(percentual, 1),
                "status_meta": status_meta,
                "track_id": track_id
            })
        
        cur.close()
        conn.close()
        return jsonify(serialize_data(stats))
    except Exception as e:
        print(f"Erro ao buscar estatísticas: {e}")
        return jsonify([]) # Retorna lista vazia para não quebrar a tela

@app.route('/api/all_songs')
def api_all_songs():
    """Retorna todas as músicas do banco (musicas_controle)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM musicas_controle ORDER BY id DESC')
        songs = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()
        return jsonify(songs)
    except Exception as e:
        print(f"Erro ao buscar músicas: {e}")
        return jsonify([])

@app.route('/api/songs/<int:song_id>', methods=['DELETE'])
def api_delete_song(song_id):
    """Deleta uma música específica do banco"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Pega o track_id antes de deletar
        cur.execute('SELECT track_id FROM musicas_controle WHERE id = %s', (song_id,))
        row = cur.fetchone()
        
        if row:
            track_id = row['track_id']
            # Deleta da tabela de controle
            cur.execute('DELETE FROM musicas_controle WHERE id = %s', (song_id,))
            # Deleta histórico diário
            cur.execute('DELETE FROM plays_diarios WHERE track_id = %s', (track_id,))
            # Deleta da playlist (fila)
            cur.execute('DELETE FROM playlist WHERE track_id = %s', (track_id,))
            
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        print(f"Erro ao deletar música: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/songs/delete_all', methods=['DELETE'])
def api_delete_all_songs():
    """Deleta TODAS as músicas do banco"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Deleta tudo
        cur.execute('DELETE FROM musicas_controle')
        cur.execute('DELETE FROM plays_diarios')
        cur.execute('DELETE FROM playlist')
        
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Todas as músicas foram deletadas!"})
    except Exception as e:
        print(f"Erro ao deletar tudo: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/plays_history/<track_id>')
def api_plays_history(track_id):
    """Retorna histórico de plays diários para um track específico"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Busca últimos 30 dias de histórico
        cur.execute('''
            SELECT data, plays FROM plays_diarios 
            WHERE track_id = %s 
            ORDER BY data DESC 
            LIMIT 30
        ''', (track_id,))
        
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        # Formata para o gráfico
        history = []
        for row in rows:
            history.append({
                "data": row['data'].strftime('%d/%m') if row['data'] else '',
                "plays": row['plays']
            })
        
        # Inverte para ordem cronológica
        history.reverse()
        
        return jsonify(history)
    except Exception as e:
        print(f"Erro ao buscar histórico: {e}")
        return jsonify([])

@app.route('/api/config', methods=['POST'])
def api_update_config():
    data = request.json
    chave = data.get('chave')
    valor = data.get('valor')
    
    if not chave or valor is None:
        return jsonify({"error": "Dados inválidos"}), 400
        
    # Salva no banco
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO config (chave, valor) VALUES (%s, %s)
        ON CONFLICT (chave) DO UPDATE SET valor = %s
    ''', (chave, str(valor), str(valor)))
    conn.commit()
    cur.close()
    conn.close()
    
    # Atualiza global
    config[chave] = int(valor) if str(valor).isdigit() else valor
    
    return jsonify({"status": "ok"})

# --- ROTAS DA INTERFACE WEB ---
def calcular_tempo_restante_fila(playlist, dispositivos_online):
    """Calcula tempo restante para completar a playlist baseado em dispositivos online"""
    tempo_total_seg = 0
    
    # Usa dispositivos online, mínimo 1 para evitar divisão por zero
    qtd_dispositivos = max(dispositivos_online, 1)
    
    for row in playlist:
        if row['status'] != 'Concluído':
            plays_restantes = row['plays_desejados'] - row['plays_atuais']
            
            if plays_restantes > 0:
                ciclos_necessarios = math.ceil(plays_restantes / qtd_dispositivos)
                intervalo_ciclo = (row['duracao_min'] * 60) + 10
                tempo_total_seg += ciclos_necessarios * intervalo_ciclo
    
    return tempo_total_seg

def calcular_tempo_planejado_fila(playlist, dispositivos_online):
    """Calcula tempo total planejado desde o início baseado em dispositivos online"""
    tempo_total_seg = 0
    
    # Usa dispositivos online, mínimo 1 para evitar divisão por zero
    qtd_dispositivos = max(dispositivos_online, 1)
    
    for row in playlist:
        if row['status'] != 'Concluído':
            plays_totais = row['plays_desejados']
            
            if plays_totais > 0:
                ciclos_necessarios = math.ceil(plays_totais / qtd_dispositivos)
                intervalo_ciclo = (row['duracao_min'] * 60) + 10
                tempo_total_seg += ciclos_necessarios * intervalo_ciclo
    
    return tempo_total_seg

@app.route('/')
def index():
    playlist = carregar_playlist()
    devices_online = contar_dispositivos_ativos()
    tempo_restante_seg = calcular_tempo_restante_fila(playlist, devices_online)
    tempo_planejado_seg = calcular_tempo_planejado_fila(playlist, devices_online)
    
    return render_template('index.html', 
                         config=config, 
                         tempo_restante_seg=tempo_restante_seg,
                         tempo_planejado_seg=tempo_planejado_seg,
                         devices_online=devices_online)

@app.route('/get_data')
def get_data():
    playlist = carregar_playlist()
    devices_online = contar_dispositivos_ativos()
    tempo_restante_seg = calcular_tempo_restante_fila(playlist, devices_online)
    tempo_planejado_seg = calcular_tempo_planejado_fila(playlist, devices_online)
    
    return jsonify({
        'playlist': playlist, 
        'config': config, 
        'tempo_restante_seg': tempo_restante_seg,
        'tempo_planejado_seg': tempo_planejado_seg,
        'devices_online': devices_online
    })

@app.route('/update_config', methods=['POST'])
def update_config():
    global config
    quantidade = int(request.form['quantidade_aparelhos'])
    config['quantidade_aparelhos'] = quantidade
    salvar_config_db('quantidade_aparelhos', quantidade)
    return redirect(url_for('index'))

@app.route('/add', methods=['POST'])
def add_music():
    salvar_musica(
        request.form['link_musica'],
        request.form['nome_musica'],
        int(request.form['plays_desejados']),
        float(request.form['duracao_min'])
    )
    return redirect(url_for('index'))

@app.route('/delete/<int:id>')
def delete_music(id):
    deletar_musica(id)
    return redirect(url_for('index'))

@app.route('/reset_all_plays')
def reset_all_plays():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE playlist SET plays_atuais = 0, status = 'Pendente'")
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('index'))

@app.route('/move_to_top/<int:id>')
def move_to_top(id):
    """Move uma música para o topo da fila"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Pega o menor ID atual
    cur.execute('SELECT MIN(id) as min_id FROM playlist')
    result = cur.fetchone()
    min_id = result['min_id'] if result and result['min_id'] else 0
    
    # Atualiza o ID da música para ser menor que o mínimo
    # (Isso é uma simplificação, em produção usaríamos uma coluna de ordem)
    cur.execute('UPDATE playlist SET id = %s WHERE id = %s', (min_id - 1, id))
    conn.commit()
    cur.close()
    conn.close()
    
    return redirect(url_for('index'))
    
@app.route('/debug/status')
def debug_status():
    """Retorna estado interno para debug"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Devices
        cur.execute(f"SELECT COUNT(*) as count FROM devices WHERE last_seen > NOW() - INTERVAL '{DEVICE_TIMEOUT_SECONDS} seconds'")
        dev_count = cur.fetchone()['count']
        
        # Devices Raw
        cur.execute("SELECT device_id, last_seen, NOW() as now, NOW() - last_seen as diff FROM devices ORDER BY last_seen DESC LIMIT 5")
        rows = cur.fetchall()
        dev_raw = []
        for row in rows:
            r = dict(row)
            r['last_seen'] = r['last_seen'].isoformat() if r['last_seen'] else None
            r['now'] = r['now'].isoformat() if r['now'] else None
            r['diff'] = str(r['diff']) # Converte timedelta para string
            dev_raw.append(r)
        
        # Playlist Queue
        cur.execute("SELECT id, nome_musica, status, plays_atuais, plays_desejados FROM playlist WHERE status != 'Concluído' ORDER BY id LIMIT 5")
        queue = [dict(row) for row in cur.fetchall()]
        
        # Playlists Table (Collections)
        cur.execute("SELECT id, nome, url FROM playlists")
        cols = [dict(row) for row in cur.fetchall()]
        
        # Count tables
        cur.execute("SELECT COUNT(*) as count FROM playlist")
        total_songs = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM playlist WHERE status = 'Em Execução'")
        executing_songs = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM musicas_controle")
        tracked_songs = cur.fetchone()['count']
        
        cur.close()
        conn.close()
        
        return jsonify({
            "devices_online_count": dev_count,
            "devices_raw": dev_raw,
            "queue_pending_top_5": queue,
            "playlists_collections_count": len(cols),
            "playlists_collections": cols,
            "total_songs_in_playlist_table": total_songs,
            "executing_songs": executing_songs,
            "tracked_songs_count": tracked_songs,
            "current_link_data": current_link_data,  # Expõe a variável global
            "config": config,
            "server_time": datetime.datetime.now().isoformat(),
            "cwd": os.getcwd(),
            "playlists_txt_exists": os.path.exists('playlists.txt'),
            "playlists_txt_abs": os.path.abspath('playlists.txt')
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- INICIALIZAÇÃO ---
# Inicializa o banco de dados quando o módulo é carregado (funciona com Gunicorn)
print("🚀 Inicializando aplicação...")
try:
    init_db()
    
    # Migração de playlists.txt se a tabela estiver vazia
    try:
        if not get_playlists_db():
            print("📂 Migrando playlists.txt para o banco...")
            if os.path.exists('playlists.txt'):
                with open('playlists.txt', 'r', encoding='utf-8') as f:
                    for linha in f:
                        url = linha.strip()
                        if url:
                            # Tenta pegar o nome via API ou usa o ID
                            nome = "Playlist Importada"
                            if sp:
                                try:
                                    pl_id = get_id_from_url(url)
                                    pl_data = sp.playlist(pl_id, fields="name")
                                    nome = pl_data['name']
                                except:
                                    pass
                            adicionar_playlist_db(url, nome)
            print("✅ Playlists migradas!")
    except Exception as e:
        print(f"⚠️ Erro na migração de playlists: {e}")
        
    carregar_config()
    print("✅ Banco de dados pronto!")
    
    # Recupera a música em execução (caso o servidor tenha reiniciado)
    playlist = carregar_playlist()
    for m in playlist:
        if m['status'] == 'Em Execução':
            current_link_data['link'] = m['link_musica']
            current_link_data['duracao_min'] = m['duracao_min']
            current_link_data['nome'] = m['nome_musica']
            current_link_data['timestamp'] = int(time.time())
            print(f"🎵 Recuperada música em execução: {m['nome_musica']}")
            break
            
except Exception as e:
    print(f"⚠️ Erro ao inicializar banco: {e}")

# Inicia o motor de automação em uma thread separada
# Inicia o motor de automação (apenas se for o main thread/process)
# Com gunicorn --workers 1, isso garante execução única
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true': # Evita duplicar no reload do flask dev
    motor_thread = threading.Thread(target=motor_automacao, daemon=True)
    motor_thread.start()

if __name__ == '__main__':
    # Inicia o servidor Flask (apenas para execução local)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
