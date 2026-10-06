# ============================================================
# VECTOR ULTRA — MOTOR RAG SEMÁNTICO EN RAM (OBSIDIAN VAULT)
# ============================================================
import os
import re
import math
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

class ObsidianRAG:
    """Indexador semántico en memoria RAM tipo BM25 para el Vault de Obsidian."""
    
    def __init__(self, vault_path: Optional[Path] = None):
        if vault_path is None:
            cands = [
                Path(r"Z:\Obsidian Vault"),
                Path(r"\\win-7l8oc5k5l2p\Vault Obsidian\Obsidian Vault"),
                Path(r"\\192.168.1.85\Vault Obsidian\Obsidian Vault"),
                Path(r"\\100.100.148.91\Vault Obsidian\Obsidian Vault"),
                Path(r"\\192.168.1.82\Vault Obsidian\Obsidian Vault"),
                Path(r"C:\Vault Obsidian"),
                Path(r"D:\Vault Obsidian")
            ]
            self.vault_path = next((p for p in cands if p.exists()), cands[0])
        else:
            self.vault_path = vault_path

        self.docs: List[Dict[str, Any]] = []
        self.doc_len: List[int] = []
        self.avg_doc_len: float = 0.0
        self.inverted_index: Dict[str, List[int]] = {}
        self.term_freqs: List[Dict[str, int]] = []
        self.total_docs: int = 0
        self.ultima_indexacion: float = 0.0
        
        self.stopwords = {
            "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
            "un", "para", "con", "no", "una", "su", "al", "lo", "como", "mas", "pero",
            "sus", "le", "ya", "o", "este", "si", "porque", "esta", "entre", "cuando",
            "muy", "sin", "sobre", "tambien", "me", "hasta", "hay", "donde", "quien",
            "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni", "contra",
            "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mi", "antes", "algunos",
            "que", "unos", "yo", "otro", "otras", "otra", "el", "tanto", "esa", "estos"
        }

    def _tokenizar(self, texto: str) -> List[str]:
        if not texto:
            return []
        s = texto.lower()
        tabla_tildes = {
            "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
            "Á": "a", "É": "e", "Í": "i", "Ó": "o", "Ú": "u",
            "ñ": "n", "Ñ": "n", "ü": "u", "Ü": "u"
        }
        for k, v in tabla_tildes.items():
            s = s.replace(k, v)
        palabras = re.findall(r"\b[a-z0-9_-]{2,}\b", s)
        return [p for p in palabras if p not in self.stopwords]

    def indexar_vault(self, forzar: bool = False) -> int:
        """Lee e indexa todas las notas .md en memoria RAM en milisegundos saltando .git."""
        if not self.vault_path.exists():
            return 0
        
        if not forzar and (time.time() - self.ultima_indexacion < 60) and self.total_docs > 0:
            return self.total_docs

        docs = []
        doc_len = []
        term_freqs = []
        inverted_index: Dict[str, List[int]] = {}

        for root, dirs, files in os.walk(self.vault_path):
            # Omitir carpetas ocultas o pesadas como .git o .obsidian
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            
            for file in files:
                if not file.endswith(".md"):
                    continue
                full_path = Path(root) / file
                try:
                    contenido = full_path.read_text(encoding="utf-8", errors="ignore")
                    tags = re.findall(r"#([a-zA-Z0-9_\-]+)", contenido)
                    
                    titulo_rep = f"{full_path.stem} " * 4
                    tags_rep = f"{' '.join(tags)} " * 2
                    texto_completo = f"{titulo_rep} {tags_rep} {contenido}"
                    
                    tokens = self._tokenizar(texto_completo)
                    if not tokens:
                        continue
                    
                    doc_idx = len(docs)
                    docs.append({
                        "titulo": full_path.stem,
                        "ruta": str(full_path),
                        "rel_path": str(full_path.relative_to(self.vault_path)),
                        "tags": tags,
                        "contenido": contenido,
                        "resumen": contenido[:280].strip().replace("\n", " ")
                    })
                    doc_len.append(len(tokens))
                    
                    tf: Dict[str, int] = {}
                    for t in tokens:
                        tf[t] = tf.get(t, 0) + 1
                        if t not in inverted_index:
                            inverted_index[t] = []
                        if not inverted_index[t] or inverted_index[t][-1] != doc_idx:
                            inverted_index[t].append(doc_idx)
                    term_freqs.append(tf)

                except Exception:
                    continue

        self.docs = docs
        self.doc_len = doc_len
        self.term_freqs = term_freqs
        self.inverted_index = inverted_index
        self.total_docs = len(docs)
        self.avg_doc_len = sum(doc_len) / self.total_docs if self.total_docs > 0 else 1.0
        self.ultima_indexacion = time.time()
        return self.total_docs

    def buscar(self, consulta: str, top_k: int = 3, k1: float = 1.5, b: float = 0.75) -> List[Dict[str, Any]]:
        """Búsqueda BM25 ultrarrápida en memoria RAM."""
        if not self.docs:
            self.indexar_vault()
        if not self.docs:
            return []

        tokens_q = self._tokenizar(consulta)
        if not tokens_q:
            return []

        puntuaciones: Dict[int, float] = {}

        for token in tokens_q:
            if token not in self.inverted_index:
                continue
            postings = self.inverted_index[token]
            n_docs_con_termino = len(postings)
            idf = math.log(1.0 + (self.total_docs - n_docs_con_termino + 0.5) / (n_docs_con_termino + 0.5))
            
            for doc_idx in postings:
                frecuencia = self.term_freqs[doc_idx].get(token, 0)
                longitud_doc = self.doc_len[doc_idx]
                numerador = frecuencia * (k1 + 1.0)
                denominador = frecuencia + k1 * (1.0 - b + b * (longitud_doc / self.avg_doc_len))
                bm25_score = idf * (numerador / denominador)
                puntuaciones[doc_idx] = puntuaciones.get(doc_idx, 0.0) + bm25_score

        if not puntuaciones:
            return []

        mejores = sorted(puntuaciones.items(), key=lambda x: x[1], reverse=True)[:top_k]
        resultados = []
        for doc_idx, score in mejores:
            d = self.docs[doc_idx]
            resultados.append({
                "titulo": d["titulo"],
                "ruta_relativa": d["rel_path"],
                "score": round(score, 3),
                "tags": d["tags"],
                "resumen": d["resumen"],
                "contenido": d["contenido"]
            })
        return resultados

    def guardar_diario_vector(self, entrada: str, fotos: Optional[List[str]] = None) -> Optional[Path]:
        try:
            ahora = datetime.now()
            dir_inbox = self.vault_path / "02 - Inbox" / "Diario Vector"
            dir_inbox.mkdir(parents=True, exist_ok=True)
            archivo_diario = dir_inbox / f"Diario Vector - {ahora.strftime('%Y-%m-%d')}.md"
            
            header = ""
            if not archivo_diario.exists():
                header = (
                    "---\n"
                    f"fecha: {ahora.strftime('%Y-%m-%d')}\n"
                    "tipo: diario-vector\n"
                    "tags: [robot, vector, log-autonomo]\n"
                    "---\n\n"
                    f"# 🤖 Diario de Campo de Vector — {ahora.strftime('%d/%m/%Y')}\n\n"
                )
            
            fotos_md = ""
            if fotos:
                for f in fotos:
                    fotos_md += f"\n![[ {Path(f).name} ]]\n"

            bloque = f"\n### ⏰ {ahora.strftime('%H:%M:%S')}\n{entrada.strip()}\n{fotos_md}\n---\n"
            
            with open(archivo_diario, "a", encoding="utf-8") as f:
                if header:
                    f.write(header)
                f.write(bloque)
            
            return archivo_diario
        except Exception as e:
            print("Error guardando diario en Vault:", e)
            return None

obsidian_rag = ObsidianRAG()
