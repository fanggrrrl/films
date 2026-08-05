import os
import re
import json
import requests
import discord
from discord.ext import commands
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil import parser

# ================= CONFIGURAÇÕES =================
LETTERBOXD_USERNAME = "fang_grrrl"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
HISTORICO_FILE = "historico.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
FORMATOS_PERMITIDOS = ["Digital HD", "Blu-ray", "Blu-ray + Digital"]
TERMOS_BLOQUEADOS = ["4K", "DVD"]

intents = discord.Intents.default()
intents.message_content = True
bot_discord = discord.Client(intents=intents)

def enviar_webhook(mensagem):
    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": mensagem})

def carregar_historico():
    if os.path.exists(HISTORICO_FILE):
        try:
            with open(HISTORICO_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def salvar_historico(historico):
    with open(HISTORICO_FILE, "w") as f:
        json.dump(historico, f, indent=4)

def normalizar(texto):
    texto = re.sub(r'[\:\-\,\.]', '', texto)
    return ' '.join(texto.lower().split())

def buscar_dvd_release(titulo, ano=""):
    slug = re.sub(r'[^a-zA-Z0-9]', '-', titulo.lower())
    slug = re.sub(r'-+', '-', slug).strip('-')
    url = f"https://www.dvdreleasedates.com/movies/{slug}/"
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            url_busca = f"https://www.dvdreleasedates.com/search.php?search={requests.utils.quote(titulo)}"
            resp_busca = requests.get(url_busca, headers=HEADERS, timeout=10)
            if resp_busca.status_code == 200:
                soup_b = BeautifulSoup(resp_busca.text, 'html.parser')
                link = soup_b.select_one('a[href*="/movies/"]')
                if link:
                    href = link.get('href')
                    url = href if href.startswith('http') else f"https://www.dvdreleasedates.com{href}"
                    resp = requests.get(url, headers=HEADERS, timeout=10)
                else:
                    return {'status': 'not_found'}
            else:
                return {'status': 'not_found'}
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        caixas = soup.find_all('td', class_='mod')
        opcoes = []

        for caixa in caixas:
            header = caixa.find('h3') or caixa.find('h2') or caixa.find('b')
            if not header:
                continue
            
            nome_formato = header.text.strip()
            if any(b in nome_formato for b in TERMOS_BLOQUEADOS):
                continue
            
            if any(p.lower() in nome_formato.lower() for p in FORMATOS_PERMITIDOS):
                data_el = caixa.find(string=re.compile(r'Release Date', re.I))
                if data_el:
                    texto_completo = data_el.parent.text if data_el.parent else ""
                    if "not announced" in texto_completo.lower():
                        continue
                    
                    match = re.search(r'Release Date\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}\s+[A-Za-z]+)', texto_completo, re.I)
                    if match:
                        dt_obj = parser.parse(match.group(1).strip())
                        is_digital = "digital" in nome_formato.lower()
                        opcoes.append({
                            'formato': "digital" if is_digital else "Blu-ray",
                            'data_str': dt_obj.strftime('%d %B'),
                            'data_obj': dt_obj
                        })

        if not opcoes:
            return {'status': 'not_announced'}

        opcoes.sort(key=lambda x: (x['data_obj'], 0 if x['formato'] == 'digital' else 1))
        melhor = opcoes[0]
        hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        return {
            'status': 'announced',
            'formato': melhor['formato'],
            'data_str': melhor['data_str'],
            'lancou': melhor['data_obj'] <= hoje
        }
    except Exception as e:
        return {'status': 'error'}

# --- RESPOSTA DIRETA AO NOME DO FILME ---
@bot_discord.event
async def on_message(message):
    if message.author == bot_discord.user:
        return

    nome_filme = message.content.strip()
    if not nome_filme:
        return

    res = buscar_dvd_release(nome_filme)
    
    if res['status'] == 'announced':
        if res['lancou']:
            await message.channel.send(f"✅ **{nome_filme}** já está disponível em {res['formato']}!")
        else:
            await message.channel.send(f"📅 **{nome_filme}** será lançado em {res['formato']} no dia **{res['data_str']}**.")
    elif res['status'] == 'not_announced':
        await message.channel.send(f"⏳ **{nome_filme}** ainda não tem data de lançamento anunciada.")
    else:
        await message.channel.send(f"❌ Não encontrei informações sobre **{nome_filme}**.")

def rodar_monitoramento():
    historico = carregar_historico()
    primeira_execucao = len(historico) == 0
    
    url = f"https://letterboxd.com/{LETTERBOXD_USERNAME}/watchlist/"
    filmes = []
    while url:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            break
        soup = BeautifulSoup(resp.text, 'html.parser')
        for item in soup.select('li.poster-container'):
            img = item.find('img')
            if img and img.has_attr('alt'):
                div = item.find('div', class_='film-poster')
                filmes.append({'titulo': img['alt'], 'ano': div.get('data-film-release-year', '') if div else ''})
        proxima = soup.select_one('a.next')
        url = f"https://letterboxd.com{proxima['href']}" if proxima else None

    for filme in filmes:
        chave = f"{normalizar(filme['titulo'])}_{filme['ano']}"
        dados = buscar_dvd_release(filme['titulo'], filme['ano'])
        
        if dados['status'] != 'announced':
            continue
            
        anterior = historico.get(chave, {})
        if primeira_execucao:
            historico[chave] = dados
            continue

        if anterior.get('status') != 'announced' or anterior.get('data_str') != dados['data_str']:
            enviar_webhook(f"**{filme['titulo']}** will be available on {dados['formato']} on {dados['data_str']}.")
            dados['notificado_lancamento'] = False
        elif dados['lancou'] and not anterior.get('notificado_lancamento', False):
            enviar_webhook(f"**{filme['titulo']}** is available now on {dados['formato']}.")
            dados['notificado_lancamento'] = True
            
        historico[chave] = dados
        
    salvar_historico(historico)

if __name__ == "__main__":
    rodar_monitoramento()
