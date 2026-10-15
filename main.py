from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import List, Optional
import re
import pandas as pd
import io

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = "postgresql://neondb_owner:npg_dNsSVOkp9o0D@ep-delicate-mode-b68ihg88-pooler.c-2.sa-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

class ComponenteItem(BaseModel):
    tipo: str
    codigo_peca: str
    marca: Optional[str] = ""
    codigos_cruzados: Optional[str] = ""

class CadastroProdutoPayload(BaseModel):
    codigo_peca: str
    marca: str
    aplicacao: str
    codigos_cruzados: str
    componentes: List[ComponenteItem]

def limpar_codigo(texto: str) -> str:
    return re.sub(r'[^A-ZA-Z0-9]', '', texto).upper()

# --- ROTA PARA BAIXAR A PLANILHA MODELO EM EXCEL ---
@app.get("/api/download-modelo")
def download_modelo():
    data = [{
        "codigo_peca": "ZEN31005",
        "marca": "ZEN",
        "aplicacao": "FORD F250 CUMMINS",
        "codigos_cruzados": "0001223505, F000AL0151, EU20571, 2C3511001BA, ZM8013006",
        "bendix": "ZEN1026",
        "rele_partida": "ZM0735",
        "rele_auxiliar": "",
        "suporte_escova": "UF12234",
        "induzido": "ZEN24037",
        "bobina": "",
        "bobina_carcaca": "ZM8930700",
        "planetaria": "UF11111",
        "reparo_planetaria": "",
        "reparo_partida": "",
        "reparo_parcial": "",
        "garfo": "",
        "mancal_acionamento": "ZM8930000",
        "mancal_intermediario": "",
        "mancal_coletor": "ZM893050",
        "rolamento_1": "",
        "rolamento_2": "",
        "kit_bucha": "KT0138",
        "pinhao": ""
    }]
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Modelo_CODIPEL')
    output.seek(0)
    
    headers = {
        'Content-Disposition': 'attachment; filename="Modelo_Importacao_CODIPEL.xlsx"'
    }
    return StreamingResponse(
        output, 
        headers=headers, 
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# --- ROTA DE BUSCA BIDIRECIONAL ---
@app.get("/api/catalogo/buscar/{termo}")
def buscar_geral(termo: str):
    termo_limpo = limpar_codigo(termo)
    if not termo_limpo:
        raise HTTPException(status_code=400, detail="Termo de busca inválido.")

    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        sql_produto = """
            SELECT id_produto, codigo_peca, marca, aplicacao, codigos_cruzados
            FROM tb_produto
            WHERE REGEXP_REPLACE(UPPER(codigo_peca), '[^A-Z0-9]', '', 'g') LIKE %s
               OR REGEXP_REPLACE(UPPER(codigos_cruzados), '[^A-Z0-9]', '', 'g') LIKE %s
        """
        param = f"%{termo_limpo}%"
        cursor.execute(sql_produto, (param, param))
        produtos_encontrados = cursor.fetchall()

        if produtos_encontrados:
            resultados = []
            for prod in produtos_encontrados:
                cursor.execute("""
                    SELECT c.tipo, c.codigo_peca, c.marca, c.codigos_cruzados
                    FROM tb_componente c
                    JOIN tb_produto_componente pc ON c.id_componente = pc.id_componente
                    WHERE pc.id_produto = %s
                """, (prod['id_produto'],))
                componentes = cursor.fetchall()
                resultados.append({
                    "tipo_resultado": "PRODUTO_COMPLETO",
                    "produto": prod,
                    "componentes": componentes
                })
            cursor.close()
            conn.close()
            return resultados

        sql_componente = """
            SELECT id_componente, tipo, codigo_peca, marca, codigos_cruzados
            FROM tb_componente
            WHERE REGEXP_REPLACE(UPPER(codigo_peca), '[^A-Z0-9]', '', 'g') LIKE %s
               OR REGEXP_REPLACE(UPPER(codigos_cruzados), '[^A-Z0-9]', '', 'g') LIKE %s
        """
        cursor.execute(sql_componente, (param, param))
        componentes_encontrados = cursor.fetchall()

        if componentes_encontrados:
            resultados = []
            for comp in componentes_encontrados:
                cursor.execute("""
                    SELECT p.id_produto, p.codigo_peca, p.marca, p.aplicacao, p.codigos_cruzados
                    FROM tb_produto p
                    JOIN tb_produto_componente pc ON p.id_produto = pc.id_produto
                    WHERE pc.id_componente = %s
                """, (comp['id_componente'],))
                produtos_aplicados = cursor.fetchall()
                resultados.append({
                    "tipo_resultado": "COMPONENTE_LINKADO",
                    "componente": comp,
                    "aplicado_em_produtos": produtos_aplicados
                })
            cursor.close()
            conn.close()
            return resultados

        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Nenhum produto ou componente encontrado.")

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

# --- ROTA DE CADASTRO MANUAL ---
@app.post("/api/cadastrar")
def cadastrar_completo(payload: CadastroProdutoPayload):
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO tb_produto (codigo_peca, marca, aplicacao, codigos_cruzados)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (codigo_peca) DO UPDATE 
            SET marca = EXCLUDED.marca, aplicacao = EXCLUDED.aplicacao, codigos_cruzados = EXCLUDED.codigos_cruzados
            RETURNING id_produto;
        """, (payload.codigo_peca, payload.marca, payload.aplicacao, payload.codigos_cruzados))
        
        id_produto = cursor.fetchone()[0]

        for item in payload.componentes:
            if not item.codigo_peca.strip():
                continue

            cursor.execute("""
                INSERT INTO tb_componente (tipo, codigo_peca, marca, codigos_cruzados)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (codigo_peca) DO UPDATE
                SET marca = EXCLUDED.marca
                RETURNING id_componente;
            """, (item.tipo, item.codigo_peca, item.marca, item.codigos_cruzados))
            
            id_componente = cursor.fetchone()[0]

            cursor.execute("""
                INSERT INTO tb_produto_componente (id_produto, id_componente)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """, (id_produto, id_componente))

        conn.commit()
        cursor.close()
        conn.close()
        return {"mensagem": "Produto e componentes salvos com sucesso!"}

    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

# --- ROTA DE IMPORTAÇÃO EM LOTE VIA EXCEL / CSV ---
@app.post("/api/cadastrar-lote")
async def cadastrar_lote(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            df = pd.read_excel(io.BytesIO(contents))
        
        df.columns = df.columns.str.strip().str.lower()
        
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        total_processados = 0
        
        mapa_componentes = {
            'bendix': 'Bendix / Impulsor',
            'rele_partida': 'Relé Partida',
            'rele_auxiliar': 'Relé Auxiliar Partida',
            'suporte_escova': 'Suporte Escova',
            'induzido': 'Induzido',
            'bobina': 'Bobina',
            'bobina_carcaca': 'Bobina c/ Carcaça',
            'planetaria': 'Planetária',
            'reparo_planetaria': 'Reparo Planetária',
            'reparo_partida': 'Reparo Partida',
            'reparo_parcial': 'Reparo Parcial Partida',
            'garfo': 'Garfo',
            'mancal_acionamento': 'Mancal Acionamento',
            'mancal_intermediario': 'Mancal Intermediário',
            'mancal_coletor': 'Mancal Coletor',
            'rolamento_1': 'Rolamento 1',
            'rolamento_2': 'Rolamento 2',
            'kit_bucha': 'Kit Bucha',
            'pinhao': 'Pinhão'
        }

        for _, row in df.iterrows():
            codigo_peca = str(row.get('codigo_peca', '')).strip()
            if not codigo_peca or codigo_peca.lower() == 'nan':
                continue
                
            marca = str(row.get('marca', '')).strip()
            if marca.lower() == 'nan': marca = ""
            
            aplicacao = str(row.get('aplicacao', '')).strip()
            if aplicacao.lower() == 'nan': aplicacao = ""
            
            codigos_cruzados = str(row.get('codigos_cruzados', '')).strip()
            if codigos_cruzados.lower() == 'nan': codigos_cruzados = ""
            
            cursor.execute("""
                INSERT INTO tb_produto (codigo_peca, marca, aplicacao, codigos_cruzados)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (codigo_peca) DO UPDATE 
                SET marca = EXCLUDED.marca, aplicacao = EXCLUDED.aplicacao, codigos_cruzados = EXCLUDED.codigos_cruzados
                RETURNING id_produto;
            """, (codigo_peca, marca, aplicacao, codigos_cruzados))
            id_produto = cursor.fetchone()[0]
            
            for col_excel, tipo_nome in mapa_componentes.items():
                if col_excel in df.columns:
                    cod_comp = str(row.get(col_excel, '')).strip()
                    if cod_comp and cod_comp.lower() != 'nan':
                        cursor.execute("""
                            INSERT INTO tb_componente (tipo, codigo_peca, marca, codigos_cruzados)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (codigo_peca) DO UPDATE
                            SET marca = EXCLUDED.marca
                            RETURNING id_componente;
                        """, (tipo_nome, cod_comp, marca, ""))
                        id_componente = cursor.fetchone()[0]
                        
                        cursor.execute("""
                            INSERT INTO tb_produto_componente (id_produto, id_componente)
                            VALUES (%s, %s)
                            ON CONFLICT DO NOTHING;
                        """, (id_produto, id_componente))
            
            total_processados += 1
            
        conn.commit()
        cursor.close()
        conn.close()
        
        return {"mensagem": f"Importação concluída com sucesso! {total_processados} produtos inseridos e vinculados."}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no processamento do ficheiro: {str(e)}")