# one-off fix — run from project root
import chromadb

client = chromadb.PersistentClient(path="./chroma_db")
client.delete_collection("techbear_facts")
client.delete_collection("techbear_voice")
print("Collections deleted.")
