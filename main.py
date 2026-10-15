from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import List, Optional
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = "postgresql://neondb_owner:npg_dNsSVOkp9o0D@ep-delicate-mode-b68ihg88-pooler.c-2.sa-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# --- MODELOS DE DADOS PARA CADASTRO ---
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

# Função para higienizar texto de busca
def limpar_codigo(texto: str) -> str:
    return re.sub(r'[^A-ZA-Z0-9]', '', texto).upper()

# --- ROTA DE BUSCA BIDIRECIONAL ---
@app.get("/api/catalogo/buscar/{termo}")
def buscar_geral(termo: str):
    termo_limpo = limpar_codigo(termo)
    if not termo_limpo:
        raise HTTPException(status_code=400, detail="Termo de busca inválido.")

    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # 1. Tenta buscar nos PRODUTOS PRINCIPAIS (pelo código único ou lista de códigos cruzados)
        sql_produto = """
            SELECT id_produto, codigo_peca, marca, aplicacao, codigos_cruzados
            FROM tb_produto
            WHERE REGEXP_REPLACE(UPPER(codigo_peca), '[^A-Z0-9]', '', 'g') LIKE %s
               OR REGEXP_REPLACE(UPPER(codigos_cruzados), '[^A-Z0-9]', '', 'g') LIKE %s
        """
        param = f"%{termo_limpo}%"
        cursor.execute(sql_produto, (param, param))
        produtos_encontrados = cursor.fetchall()

        # Se encontrou produtos principais, traz os detalhes e a lista de componentes de cada um
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

        # 2. Se não achou no Produto, busca nos COMPONENTES (ex: digitou código do Relé ZM0735)
        sql_componente = """
            SELECT id_componente, tipo, codigo_peca, marca, codigos_cruzados
            FROM tb_componente
            WHERE REGEXP_REPLACE(UPPER(codigo_peca), '[^A-Z0-9]', '', 'g') LIKE %s
               OR REGEXP_REPLACE(UPPER(codigos_cruzados), '[^A-Z0-9]', '', 'g') LIKE %s
        """
        cursor.execute(sql_componente, (param, param))
        componentes_encontrados = cursor.fetchall()

        if componentes_encontrados:
            # Para cada componente, descobre em quais produtos ele é aplicado
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

# --- ROTA DE CADASTRO COMPLETO ---
@app.post("/api/cadastrar")
def cadastrar_completo(payload: CadastroProdutoPayload):
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    try:
        # Insere ou recupera o Produto Principal
        cursor.execute("""
            INSERT INTO tb_produto (codigo_peca, marca, aplicacao, codigos_cruzados)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (codigo_peca) DO UPDATE 
            SET marca = EXCLUDED.marca, aplicacao = EXCLUDED.aplicacao, codigos_cruzados = EXCLUDED.codigos_cruzados
            RETURNING id_produto;
        """, (payload.codigo_peca, payload.marca, payload.aplicacao, payload.codigos_cruzados))
        
        id_produto = cursor.fetchone()[0]

        # Processa cada componente do formulário
        for item in payload.componentes:
            if not item.codigo_peca.strip():
                continue

            # Insere o componente (ou atualiza se já existir)
            cursor.execute("""
                INSERT INTO tb_componente (tipo, codigo_peca, marca, codigos_cruzados)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (codigo_peca) DO UPDATE
                SET marca = EXCLUDED.marca, codigos_cruzados = EXCLUDED.codigos_cruzados
                RETURNING id_componente;
            """, (item.tipo, item.codigo_peca, item.marca, item.codigos_cruzados))
            
            id_componente = cursor.fetchone()[0]

            # Vincula produto ao componente
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