import os
import math
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_cors import CORS
import threading
import time

# --- CONFIGURAÇÃO ---
app = Flask(__name__, template_folder='templates')
CORS(app)  # Permite requisições do Flutter

# Configuração do banco de dados
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://localhost/loop_playlist')

# Variável global para o link atual (atualizada pelo motor)
current_link_data = {
    "link": "",
    "duracao_min": 3.0,
    "nome": "",
    "timestamp": 0
}

# Configurações globais
config = {
    "quantidade_aparelhos": 200
}

# --- FUNÇÕES DO BANCO DE DADOS ---
def get_db_connection():
    """Cria conexão com o PostgreSQL"""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    """Inicializa as tabelas do banco de dados"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Tabela de músicas/playlist
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
    
    # Inserir configuração padrão se não existir
    cur.execute('''
        INSERT INTO config (chave, valor) VALUES ('quantidade_aparelhos', '200')
        ON CONFLICT (chave) DO NOTHING
    ''')
    
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Banco de dados inicializado!")

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
    """Conta dispositivos que fizeram heartbeat nos últimos 60 segundos"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT COUNT(*) as count FROM devices 
        WHERE last_seen > NOW() - INTERVAL '60 seconds'
    ''')
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result['count'] if result else 0


# --- MOTOR DE AUTOMAÇÃO ---
def motor_automacao():
    """Loop principal que processa a playlist"""
    global current_link_data
    print(">>> Motor de automação iniciado. <<<")
    
    # Aguarda o banco estar pronto
    time.sleep(5)
    
    while True:
        try:
            playlist = carregar_playlist()
            carregar_config()
            
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
                        "timestamp": int(time.time())
                    }
                    
                    # Usa dispositivos online em vez de aparelhos configurados
                    dispositivos_online = contar_dispositivos_ativos()
                    if dispositivos_online == 0:
                        dispositivos_online = 1  # Mínimo 1 para evitar divisão por zero
                    
                    print(f"[{time.strftime('%H:%M:%S')}] Enviando '{musica_atual['nome_musica']}' | "
                          f"Dispositivos online: {dispositivos_online} | "
                          f"Progresso: {musica_atual['plays_atuais'] + dispositivos_online}/{musica_atual['plays_desejados']}")
                    
                    # Calcula plays a somar baseado em dispositivos online
                    plays_a_somar = min(
                        dispositivos_online,
                        musica_atual['plays_desejados'] - musica_atual['plays_atuais']
                    )
                    
                    # Atualiza os plays no banco
                    atualizar_musica(
                        musica_id,
                        musica_atual['plays_atuais'] + plays_a_somar,
                        musica_atual['plays_mensais'] + plays_a_somar,
                        'Em Execução'
                    )
                    
                    # Aguarda o tempo do ciclo
                    time.sleep(intervalo_ciclo_seg)
                else:
                    # Concluiu todos os plays
                    atualizar_musica(
                        musica_id,
                        musica_atual['plays_atuais'],
                        musica_atual['plays_mensais'],
                        'Concluído'
                    )
                    time.sleep(1)
            else:
                # Não há músicas na fila
                time.sleep(30)
                
        except Exception as e:
            print(f"ERRO NO MOTOR: {e}")
            time.sleep(15)

# --- API ENDPOINTS PARA O FLUTTER ---
@app.route('/api/current_link')
def api_current_link():
    """Retorna o link atual para os dispositivos Flutter"""
    device_id = request.args.get('device_id', 'unknown')
    
    # Registra heartbeat do dispositivo
    try:
        registrar_heartbeat(device_id)
    except:
        pass
    
    return jsonify(current_link_data)

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

# --- INICIALIZAÇÃO ---
# Inicializa o banco de dados quando o módulo é carregado (funciona com Gunicorn)
print("🚀 Inicializando aplicação...")
try:
    init_db()
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
motor_thread = threading.Thread(target=motor_automacao, daemon=True)
motor_thread.start()

if __name__ == '__main__':
    # Inicia o servidor Flask (apenas para execução local)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
