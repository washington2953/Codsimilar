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
    sobrescrever: Optional[bool] = False

def limpar_codigo(texto: str) -> str:
    if not texto or texto.lower() == 'nan':
        return ""
    return re.sub(r'[^A-ZA-Z0-9]', '', str(texto)).upper()

@app.get("/api/download-modelo")
def download_modelo():
    data = [{
        "codigo_peca": "0001109300",
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
    
    headers = {'Content-Disposition': 'attachment; filename="Modelo_Importacao_CODIPEL.xlsx"'}
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

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

@app.post("/api/cadastrar")
def cadastrar_completo(payload: CadastroProdutoPayload):
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cod_peca = payload.codigo_peca.strip().upper()
        marca = payload.marca.strip().upper()
        aplicacao = payload.aplicacao.strip().upper()
        codigos_cruzados = payload.codigos_cruzados.strip().upper()

        cursor.execute("SELECT * FROM tb_produto WHERE UPPER(codigo_peca) = %s;", (cod_peca,))
        existente = cursor.fetchone()

        if existente and not payload.sobrescrever:
            # Verifica se há divergência de dados
            if existente['marca'] != marca or existente['aplicacao'] != aplicacao:
                cursor.close()
                conn.close()
                return {
                    "status": "CONFLITO_DUPLICADO",
                    "mensagem": "Este produto já existe no banco de dados com informações diferentes.",
                    "dados_antigos": existente,
                    "dados_novos": {
                        "codigo_peca": cod_peca,
                        "marca": marca,
                        "aplicacao": aplicacao,
                        "codigos_cruzados": codigos_cruzados
                    }
                }

        # Insere ou atualiza o produto principal
        cursor.execute("""
            INSERT INTO tb_produto (codigo_peca, marca, aplicacao, codigos_cruzados)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (codigo_peca) DO UPDATE 
            SET marca = EXCLUDED.marca, 
                aplicacao = EXCLUDED.aplicacao, 
                codigos_cruzados = COALESCE(tb_produto.codigos_cruzados, '') || ', ' || EXCLUDED.codigos_cruzados
            RETURNING id_produto;
        """, (cod_peca, marca, aplicacao, codigos_cruzados))
        
        id_produto = cursor.fetchone()['id_produto']

        # Processa os componentes sem apagar os existentes
        for item in payload.componentes:
            c_cod = item.codigo_peca.strip().upper()
            if not c_cod:
                continue

            c_tipo = item.tipo.strip()
            c_marca = item.marca.strip().upper()

            cursor.execute("""
                INSERT INTO tb_componente (tipo, codigo_peca, marca, codigos_cruzados)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (codigo_peca) DO UPDATE
                SET marca = CASE WHEN EXCLUDED.marca != '' THEN EXCLUDED.marca ELSE tb_componente.marca END
                RETURNING id_componente;
            """, (c_tipo, c_cod, c_marca, ""))
            
            id_componente = cursor.fetchone()['id_componente']

            cursor.execute("""
                INSERT INTO tb_produto_componente (id_produto, id_componente)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """, (id_produto, id_componente))

        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "SUCESSO", "mensagem": "Produto e componentes salvos e unificados com sucesso!"}

    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cadastrar-lote")
async def cadastrar_lote(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        
        # dtype=str Garante que zeros à esquerda não sejam apagados
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(contents), dtype=str)
        
        # Converte tudo para texto limpo e MAIÚSCULO
        df = df.fillna("").astype(str)
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
            codigo_peca = str(row.get('codigo_peca', '')).strip().upper()
            if not codigo_peca or codigo_peca == 'NAN':
                continue
                
            marca = str(row.get('marca', '')).strip().upper()
            aplicacao = str(row.get('aplicacao', '')).strip().upper()
            codigos_cruzados = str(row.get('codigos_cruzados', '')).strip().upper()
            
            cursor.execute("""
                INSERT INTO tb_produto (codigo_peca, marca, aplicacao, codigos_cruzados)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (codigo_peca) DO UPDATE 
                SET marca = CASE WHEN EXCLUDED.marca != '' THEN EXCLUDED.marca ELSE tb_produto.marca END,
                    aplicacao = CASE WHEN EXCLUDED.aplicacao != '' THEN EXCLUDED.aplicacao ELSE tb_produto.aplicacao END,
                    codigos_cruzados = COALESCE(tb_produto.codigos_cruzados, '') || ', ' || EXCLUDED.codigos_cruzados
                RETURNING id_produto;
            """, (codigo_peca, marca, aplicacao, codigos_cruzados))
            id_produto = cursor.fetchone()[0]
            
            for col_excel, tipo_nome in mapa_componentes.items():
                if col_excel in df.columns:
                    cod_comp = str(row.get(col_excel, '')).strip().upper()
                    if cod_comp and cod_comp != 'NAN':
                        cursor.execute("""
                            INSERT INTO tb_componente (tipo, codigo_peca, marca, codigos_cruzados)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (codigo_peca) DO UPDATE
                            SET marca = CASE WHEN EXCLUDED.marca != '' THEN EXCLUDED.marca ELSE tb_componente.marca END
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
        
        return {"mensagem": f"Importação em lote concluída! {total_processados} produtos processados e mesclados com sucesso."}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na importação: {str(e)}")