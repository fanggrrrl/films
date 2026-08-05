import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil import parser

# ================= CONFIGURAÇÕES =================
LETTERBOXD_USERNAME = "fang_grrrl"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
HISTORICO_FILE = "historico.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
FORMATOS_PERMITIDOS = ["Digital HD", "Blu-ray", "Blu-ray + Digital"]
TERMOS_BLOQUEADOS = ["4K", "DVD"]

def enviar_discord(mensagem):
    if not DISCORD_WEBHOOK_URL:
        print("Aviso: URL do Webhook do Discord não configurada.")
        return
    payload = {"content": mensagem}
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"Erro ao enviar mensagem para o Discord: {e}")

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

def obter_watchlist():
    filmes = []
    url = f"https://letterboxd.com/{LETTERBOXD_USERNAME}/watchlist/"
    while url:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
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
        except Exception as e:
            print(f"Erro na raspagem do Letterboxd: {e}")
            break
    return filmes

def buscar_dvd_release(titulo, ano):
    slug = re.sub(r'[^a-zA-Z0-9]', '-', titulo.lower())
    slug = re.sub(r'-+', '-', slug).strip('-')
    url = f"https://www.dvdreleasedates.com/movies/{slug}/"
    
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
                return None
        else:
            return None

    if resp.status_code != 200:
        return None

    try:
        soup = BeautifulSoup(resp.text, 'html.parser')
        caixas = soup.find_all(['td', 'div'])
        opcoes_encontradas = []

        for caixa in caixas:
            texto_caixa = caixa.get_text(separator=' ', strip=True)
            
            formato_match = None
            for fmt in FORMATOS_PERMITIDOS:
                if fmt.lower() in texto_caixa.lower():
                    formato_match = fmt
                    break
            
            if not formato_match:
                continue

            if any(b.lower() in texto_caixa.lower() for b in TERMOS_BLOQUEADOS if b.lower() not in formato_match.lower()):
                continue

            if "not announced" in texto_caixa.lower():
                continue

            # IGNORA DATAS ESTIMADAS: Só aceita a data se NÃO tiver 'est' após o Release Date
            if re.search(r'Release Date\s+est\b', texto_caixa, re.I):
                continue

            # Captura apenas datas reais/confirmadas
            match = re.search(
                r'Release Date\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{4})',
                texto_caixa,
                re.I
            )

            if match:
                str_data = match.group(1).strip()
                try:
                    dt_obj = parser.parse(str_data)
                    is_digital = "digital" in formato_match.lower()
                    opcoes_encontradas.append({
                        'formato': "Digital HD" if is_digital else "Blu-ray",
                        'data_str': dt_obj.strftime('%B %d, %Y') if re.search(r'\d{1,2}', str_data) else dt_obj.strftime('%B %Y'),
                        'data_obj': dt_obj
                    })
                except:
                    pass

        if not opcoes_encontradas:
            return {'status': 'not announced'}

        opcoes_encontradas.sort(key=lambda x: (x['data_obj'], 0 if x['formato'] == 'Digital HD' else 1))
        melhor_opcao = opcoes_encontradas[0]
        
        hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        ja_lancou = melhor_opcao['data_obj'] <= hoje
        
        return {
            'status': 'announced',
            'formato': melhor_opcao['formato'],
            'data_str': melhor_opcao['data_str'],
            'lancou': ja_lancou
        }
    except Exception as e:
        print(f"Erro ao processar {titulo}: {e}")
        return None

def main():
    historico = carregar_historico()
    primeira_execucao = False
    
    filmes = obter_watchlist()
    print(f"Total de filmes na Watchlist: {len(filmes)}")
    
    for filme in filmes:
        chave = f"{normalizar(filme['titulo'])}_{filme['ano']}"
        dados_site = buscar_dvd_release(filme['titulo'], filme['ano'])
        
        if not dados_site or dados_site.get('status') != 'announced':
            continue
            
        estado_anterior = historico.get(chave, {})
        
        if primeira_execucao:
            historico[chave] = dados_site
            continue
        
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
