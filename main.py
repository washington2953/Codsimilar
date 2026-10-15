from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = "postgresql://seu_usuario:sua_senha@ep-seu-host.us-east-2.aws.neon.tech/neondb?sslmode=require"

# --- ESTRUTURA DOS DADOS QUE O SITE VAI ENVIAR ---
class ComponenteInput(BaseModel):
    nome: str
    codigo_referencia: str
    quantidade: int

class ProdutoInput(BaseModel):
    descricao: str
    codigo_original: str
    id_categoria: int = 1  # 1 para Elétrica/Motor, etc.
    componentes: List[ComponenteInput]

# --- ROTA DE CONSULTA (JÁ EXISTENTE) ---
@app.get("/api/catalogo/{codigo_produto}")
def buscar_pecas(codigo_produto: str):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        sql = """
            SELECT 
                p.descricao AS produtoprincipal,
                c.codigo_referencia AS codigopeca,
                c.nome AS subcomponente,
                comp.quantidade
            FROM tb_produto p
            INNER JOIN tb_produto_codigo pc ON p.id_produto = pc.id_produto
            INNER JOIN tb_composicao comp ON p.id_produto = comp.id_produto
            INNER JOIN tb_componente c ON comp.id_componente = c.id_componente
            WHERE pc.codigo_original = %s
        """
        cursor.execute(sql, (codigo_produto,))
        resultados = cursor.fetchall()
        cursor.close()
        conn.close()
        if not resultados:
            raise HTTPException(status_code=404, detail="Peça não encontrada.")
        return resultados
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- NOVA ROTA DE CADASTRO ---
@app.post("/api/cadastrar")
def cadastrar_produto(dados: ProdutoInput):
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    try:
        # 1. Insere o Produto Principal
        cursor.execute(
            "INSERT INTO tb_produto (id_categoria, descricao) VALUES (%s, %s) RETURNING id_produto;",
            (dados.id_categoria, dados.descricao)
        )
        id_produto = cursor.fetchone()[0]

        # 2. Vincula o Código Original ao Produto
        cursor.execute(
            "INSERT INTO tb_produto_codigo (id_produto, codigo_original) VALUES (%s, %s);",
            (id_produto, dados.codigo_original)
        )

        # 3. Insere os Componentes e Vincula na Composição
        for comp in dados.componentes:
            cursor.execute(
                "INSERT INTO tb_componente (nome, codigo_referencia) VALUES (%s, %s) RETURNING id_componente;",
                (comp.nome, comp.codigo_referencia)
            )
            id_componente = cursor.fetchone()[0]

            cursor.execute(
                "INSERT INTO tb_composicao (id_produto, id_componente, quantidade) VALUES (%s, %s, %s);",
                (id_produto, id_componente, comp.quantidade)
            )

        conn.commit()  # Salva todas as alterações no banco
        cursor.close()
        conn.close()
        return {"mensagem": "Produto e componentes cadastrados com sucesso!"}

    except Exception as e:
        conn.rollback()  # Cancela em caso de erro para não poluir o banco
        cursor.close()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erro ao cadastrar: {str(e)}")