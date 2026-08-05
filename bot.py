import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil import parser

# ================= CONFIGURAÇÕES =================
LETTERBOXD_USERNAME = "fang_grrrl"  # <-- COLOQUE SEU USUÁRIO DO LETTERBOXD AQUI
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
HISTORICO_FILE = "historico.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
FORMATOS_PERMITIDOS = ["Digital HD", "Blu-ray", "Blu-ray + Digital"]
TERMOS_BLOQUEADOS = ["4K", "DVD"]

def enviar_discord(mensagem):
    payload = {"content": mensagem}
    requests.post(DISCORD_WEBHOOK_URL, json=payload)

def carregar_historico():
    if os.path.exists(HISTORICO_FILE):
        with open(HISTORICO_FILE, "r") as f:
            return json.load(f)
    return {}

def salvar_historico(historico):
    with open(HISTORICO_FILE, "w") as f:
        json.dump(historico, f, indent=4)

def normalizar(texto):
    texto = re.sub(r'[\:\-\,\.]', '', texto)
    return ' '.join(texto.lower().split())

def obter_watchlist():
    filmes = []
    url = f"https://letterboxd.com/{LETTERBOXD_USERNAME}/watchlist/"
    while url:
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code != 200:
            break
        soup = BeautifulSoup(resp.text, 'html.parser')
        itens = soup.select('li.poster-container')
        for item in itens:
            img = item.find('img')
            if img and img.has_attr('alt'):
                titulo = img['alt']
                div = item.find('div', class_='film-poster')
                ano = div.get('data-film-release-year', '') if div else ''
                filmes.append({'titulo': titulo, 'ano': ano})
        
        proxima = soup.select_one('a.next')
        url = f"https://letterboxd.com{proxima['href']}" if proxima else None
    return filmes

def buscar_dvd_release(titulo, ano):
    slug = re.sub(r'[^a-zA-Z0-9]', '-', titulo.lower())
    slug = re.sub(r'-+', '-', slug).strip('-')
    url = f"https://www.dvdreleasedates.com/movies/{slug}/"
    
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        return None
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    header_h1 = soup.find('h1')
    if header_h1 and ano:
        if ano not in header_h1.text:
            return None

    caixas = soup.find_all('td', class_='mod')
    opcoes_encontradas = []

    for caixa in caixas:
        header = caixa.find('h3') or caixa.find('h2') or caixa.find('b')
        if not header:
            continue
        
        nome_formato = header.text.strip()
        
        if any(b in nome_formato for b in TERMOS_BLOQUEADOS):
            continue
        
        if any(p.lower() in nome_formato.lower() for p.lower() in FORMATOS_PERMITIDOS):
            data_el = caixa.find(text=re.compile(r'Release Date', re.I))
            if data_el:
                parent = data_el.parent
                texto_completo = parent.text if parent else ""
                
                if "not announced" in texto_completo.lower():
                    continue
                
                match = re.search(r'Release Date\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}\s+[A-Za-z]+)', texto_completo, re.I)
                if match:
                    str_data = match.group(1).strip()
                    try:
                        dt_obj = parser.parse(str_data)
                        is_digital = "digital" in nome_formato.lower()
                        opcoes_encontradas.append({
                            'formato': "digital" if is_digital else "Blu-ray",
                            'data_str': dt_obj.strftime('%d %B'),
                            'data_obj': dt_obj
                        })
                    except:
                        pass

    if not opcoes_encontradas:
        return {'status': 'not announced'}

    opcoes_encontradas.sort(key=lambda x: (x['data_obj'], 0 if x['formato'] == 'digital' else 1))
    melhor_opcao = opcoes_encontradas[0]
    
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ja_lancou = melhor_opcao['data_obj'] <= hoje
    
    return {
        'status': 'announced',
        'formato': melhor_opcao['formato'],
        'data_str': melhor_opcao['data_str'],
        'lancou': ja_lancou
    }

def main():
    historico = carregar_historico()
    primeira_execucao = len(historico) == 0
    
    filmes = obter_watchlist()
    
    for filme in filmes:
        chave = f"{normalizar(filme['titulo'])}_{filme['ano']}"
        dados_site = buscar_dvd_release(filme['titulo'], filme['ano'])
        
        if not dados_site:
            continue
            
        estado_anterior = historico.get(chave, {})
        
        if primeira_execucao:
            historico[chave] = dados_site
            continue
        
        if dados_site['status'] == 'announced':
            data_mudou = estado_anterior.get('data_str') != dados_site['data_str']
            status_anterior = estado_anterior.get('status', 'not announced')
            ja_notificado_lancamento = estado_anterior.get('notificado_lancamento', False)
            
            if status_anterior == 'not announced' or data_mudou:
                msg = f"**{filme['titulo']}** will be available on {dados_site['formato']} on {dados_site['data_str']}."
                enviar_discord(msg)
                dados_site['notificado_lancamento'] = False
            
            elif dados_site['lancou'] and not ja_notificado_lancamento:
                msg = f"**{filme['titulo']}** is available now on {dados_site['formato']}."
                enviar_discord(msg)
                dados_site['notificado_lancamento'] = True
            else:
                dados_site['notificado_lancamento'] = ja_notificado_lancamento
                
        historico[chave] = dados_site
        
    salvar_historico(historico)

if __name__ == "__main__":
    main()
