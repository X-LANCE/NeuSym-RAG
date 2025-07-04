#coding=utf8
import numpy as np
import duckdb, math, tqdm, time
from scipy.sparse import csr_array
import os, re, sys, json, logging, torch, time
from pymilvus import MilvusClient, FieldSchema, CollectionSchema, DataType
from milvus_model.base import BaseEmbeddingFunction
from typing import List, Tuple, Dict, Any, Union, Optional
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.config import DATASET_DIR, DATABASE2DATASET, VECTORSTORE_DIR, CACHE_DIR
from utils.database_utils import get_database_connection
from utils.database_schema import DatabaseSchema
from utils.vectorstore_schema import VectorstoreSchema, VectorstoreCollection
from utils.functions.ai_research_metadata import get_airqa_paper_metadata


logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter(
    fmt='[%(asctime)s][%(filename)s - %(lineno)d][%(levelname)s]: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


EMBED_TYPES = {
    'text': ['sentence_transformers', 'bge', 'instructor', 'mgte', 'bm25', 'splade'],
    'image': ['clip']
}


GLOBAL_EMBEDDING_MODELS = dict()


def detect_embedding_model_path(model_name: str) -> str:
    """ Given the `model_name`, find the cached model folder under the .cache/ folder.
    """
    if os.path.exists(model_name) and os.path.isdir(model_name):
        return model_name
    files = os.listdir(CACHE_DIR)
    model_name = os.path.basename(model_name.rstrip(os.sep).strip())
    if model_name in files:
        return os.path.join(CACHE_DIR, model_name)
    else:
        normed_model_name = re.sub(r'[^a-z0-9_]', '_', model_name.lower())
        normed_files = {
            re.sub(r'[^a-z0-9_]', '_', m.lower().rstrip(os.sep).strip()): m
            for m in files if os.path.isdir(os.path.join(CACHE_DIR, m))
        }
        detect_model_name = normed_files.get(normed_model_name, None)
        if detect_model_name is None:
            raise ValueError(f"[Error]: Embedding model {model_name} not found in the {CACHE_DIR} folder.")
        # logger.info(f"Found cached embedding model `{detect_model_name}` for `{model_name}`.")
        return os.path.join(CACHE_DIR, detect_model_name)


def get_embed_model_from_collection(
        collection_name: Union[str, List[str]] = None,
        client: Optional[MilvusClient] = None) -> Union[List[Dict[str, str]], Dict[str, str]]:
    """ Get the embedding model modality, type and name from the collection name.
    @args:
        collection_name: Union[str, List[str]], the collection name or name lsit to parse, e.g., `text_sentence_transformers_all-MiniLM-L6-v2`
        client: MilvusClient, the connection to the vectorstore, used to get all collections if collection_name is None
    @return:
        embed_kwargs: Union[List[Dict[str, str]], Dict[str, str]], the parsed embedding model type and name, each encapulated in a dict with the following fields:
            {
                "collection_name": xxx,
                "modality": "text", // chosen from ['text', 'image']
                "embed_type": xxx, // see EMBED_TYPES
                "embed_model": xxx // models should be downloaded in the local .cache/ folder
            }
    """
    if collection_name is None:
        collection_name = client.list_collections()
    if isinstance(collection_name, str):
        collection_name = [collection_name]

    embed_kwargs = []
    for collection in collection_name:
        modality = 'text' if collection.startswith('text') else 'image'
        embed_type_and_model = collection[len(modality) + 1:]
        for et in EMBED_TYPES[modality]:
            if embed_type_and_model.startswith(et):
                embed_type = et
                embed_model = embed_type_and_model[len(et) + 1:]
                break
        else:
            raise ValueError(f"Cannot determine embedding model type from collection name `{collection}`.")

        embed_model = detect_embedding_model_path(embed_model) if embed_type != 'bm25' else embed_model
        embed_kwargs.append({
            'collection': collection,
            'modality': modality,
            'embed_type': embed_type,
            'embed_model': embed_model
        })

    return embed_kwargs


def get_milvus_embedding_function(
    embed_type: str = 'sentence_transformers',
    embed_model: str = 'all-MiniLM-L6-v2',
    backup_json: str = None
) -> BaseEmbeddingFunction:
    """ Note that, we only support open-source embedding models w/o the need of API keys.
    """
    if (embed_type, embed_model, str(backup_json)) in GLOBAL_EMBEDDING_MODELS:
        return GLOBAL_EMBEDDING_MODELS[(embed_type, embed_model, str(backup_json))]

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    embed_model = detect_embedding_model_path(embed_model) if embed_type != 'bm25' else embed_model

    if embed_type == 'sentence_transformers':
        from milvus_model.dense import SentenceTransformerEmbeddingFunction
        embed_func = SentenceTransformerEmbeddingFunction(
            model_name=embed_model,
            device=device
        )
    elif embed_type == 'bge':
        from milvus_model.hybrid import BGEM3EmbeddingFunction
        embed_func = BGEM3EmbeddingFunction(
            model_name=embed_model,
            device=device,
            use_fp16=False
        )
    elif embed_type == 'instructor':
        from milvus_model.dense import InstructorEmbeddingFunction
        embed_func = InstructorEmbeddingFunction(
            model_name=embed_model,
            device=device,
            query_instruction='Represent the question for retrieval: ',
            doc_instruction='Represent the document text for retrieval: '
        )
    elif embed_type == 'mgte':
        from milvus_model.hybrid import MGTEEmbeddingFunction
        embed_func = MGTEEmbeddingFunction(
            model_name=embed_model,
            device=device
        )
    elif embed_type == 'bm25':
        from milvus_model.sparse import BM25EmbeddingFunction
        from milvus_model.sparse.bm25.tokenizers import build_default_analyzer
        en_analyzer = build_default_analyzer(language=embed_model)
        embed_func = BM25EmbeddingFunction(analyzer=en_analyzer)
        # need to invoke another function to gather statistics of the entire corpus before encoding
        # embed_func.fit(corpus: List[str])
        # or, directly load the pre-stored BM25 statistics
        if backup_json is not None and os.path.exists(backup_json):
            # logger.info(f"Load BM25 model from {backup_json} ...")
            embed_func.load(backup_json)
    elif embed_type == 'splade':
        from milvus_model.sparse import SpladeEmbeddingFunction
        embed_func = SpladeEmbeddingFunction(
            model_name=embed_model,
            device=device
        )
    elif embed_type == 'clip':
        from utils.embedding_utils import ClipEmbeddingFunction
        embed_func = ClipEmbeddingFunction(embed_model, device=device)
    else:
        raise ValueError(f"Unsupported embedding model type: {embed_type}. We only support {list(EMBED_TYPES.keys())}.")

    GLOBAL_EMBEDDING_MODELS[(embed_type, embed_model, str(backup_json))] = embed_func
    return embed_func


def get_vectorstore_connection(
        vectorstore_name: str,
        launch_method: str = 'standalone',
        docker_uri: str = 'http://127.0.0.1:19530',
        vectorstore_path: Optional[str] = None,
        from_scratch: bool = False
    ) -> MilvusClient:
    """ Get the connection to the vectorstore, either from the local .db path (Milvus-lite) or from the remote server via Docker.
    @args:
        vectorstore_name: str, the database name to create/use
        launch_method: str, the launch method for the Milvus server, chosen from ['standalone', 'docker']
        docker_uri: str, the URI for the Milvus server if using Docker
        vectorstore_path: str, the path to the vectorstore if using Milvus-lite
        from_scratch: bool, remove the existed vectorstore with `vectorstore_name` or not
    @return:
        conn: MilvusClient, the connection to the vectorstore
    """
    assert launch_method in ['docker', 'standalone'], f"Vectorstore launch method {launch_method} not supported."
    if launch_method == 'standalone': # Milvus-lite
        vs_path = vectorstore_path if vectorstore_path is not None else \
            os.path.join(VECTORSTORE_DIR, vectorstore_name, f'{vectorstore_name}.db')
        if from_scratch and os.path.exists(vs_path):
            os.remove(vs_path)
        client = MilvusClient(vs_path)
    else:
        client = MilvusClient(docker_uri)
        dbs = client.list_databases()
        if from_scratch and vectorstore_name in dbs:
            client.using_database(vectorstore_name)
            collections = client.list_collections()
            for col in collections:
                client.drop_collection(col)
            client.drop_database(vectorstore_name)
        if from_scratch or vectorstore_name not in dbs:
            client.create_database(vectorstore_name)
        client.using_database(vectorstore_name)
    return client


def initialize_vectorstore(conn: MilvusClient, schema: VectorstoreSchema) -> MilvusClient:
    """ Initialize the schema for the Milvus vectorstore, including the collections, fields and their types.
    """
    collections = conn.list_collections()
    for collection_name in schema.collections:
        if collection_name in collections:
            logger.warning(f'Colelction {collection_name} already exists, skip creation.')
            continue

        collection: VectorstoreCollection = schema.get_collection(collection_name)

        # add schema fields
        collection_fields = [FieldSchema(**field.to_dict()) for field in collection.fields]
        collection_schema = CollectionSchema(
            collection_fields,
            description=collection.description,
            enable_dynamic_field=False
        )

        # add index params
        index_params = MilvusClient.prepare_index_params()
        for index_obj in collection.indexes:
            index_params.add_index(**index_obj.to_dict())

        # create collection from the customized schema fields and indexes
        conn.create_collection(
            collection_name,
            schema=collection_schema,
            index_params=index_params
        )
        time.sleep(5)

        logger.info(f"Create collection {collection_name}: {conn.describe_collection(collection_name)}")

    return conn


def retrieve_cell_values(
        db_conn: duckdb.DuckDBPyConnection,
        table_name: str,
        column_name: str,
        primary_keys: Union[str, List[str]],
        pdf_and_page_fields: Tuple[str] = (),
        where_condition: str = ''
    ) -> List[Tuple[Any]]:
    """ Execute the SQL statement to retrieve all column values, and also obtain the primary key values and pdf_id/page_id cell values.
    @args:
        db_conn: duckdb.DuckDBPyConnection, the connection to the relational database
        table_name: str, the table name to query
        column_name: str, the column name to retrieve
        primary_keys: Union[str, List[str]], the primary key names (`str` for single-column primary key)
        pdf_and_page_fields: Tuple[str], pdf_id column name and page_id column name (None if not exists)
        where_condition: str, where condition string used to filter the execution result (excluding prefix `WHERE`)
    @return:
        List[Tuple[Any]], the returned results
    """
    primary_keys = [primary_keys] if type(primary_keys) == str else primary_keys
    assert len(pdf_and_page_fields) == 2 and pdf_and_page_fields[0] is not None, f"PDF id field not found in table {table_name}."
    extra_fields = ', '.join(pdf_and_page_fields) if pdf_and_page_fields[1] is not None else pdf_and_page_fields[0]
    select = f"SELECT {column_name}, {extra_fields}, {', '.join(primary_keys)} FROM {table_name}"
    if where_condition: select += f" WHERE {where_condition}"
    try:
        result = db_conn.sql(select).fetchall()
        return result
    except Exception as e:
        logger.error(f'[Error]: Failed to execute SQL query: {select}')
        return []


def get_page_number_from_id(database_name: str, db_conn: duckdb.DuckDBPyConnection, pdf_id: str, page_id: str) -> int:
    """ Get the page number from the relational database.
    @args:
        database_name: str, the relational database name
        db_conn: duckdb.DuckDBPyConnection, the connection to the relational database
        pdf_id: str, the PDF id
        page_id: str, the page id
    @return:
        int, the page number (-1 means not found)
    """
    sql_query = {
        'ai_research': f"SELECT page_number FROM pages WHERE ref_paper_id = '{pdf_id}' AND page_id = '{page_id}';"
    }
    select = sql_query.get(database_name, sql_query['ai_research'])
    try:
        result = db_conn.sql(select).fetchall()
        return int(result[0][0])
    except:
        logger.error(f"Failed to get page_number from page_id `{page_id}` for DB {database_name} with pdf_id `{pdf_id}`.")
        return -1


def get_image_or_pdf_path(database: str, pdf_id: str) -> str:
    """ Get the image or PDF path for the database.
    @args:
        database: str, the database name
        pdf_id: str, the PDF id
    @return:
        str: The file path for the PDF or image.
    """
    # Load the mapping file
    dataset = DATABASE2DATASET[database]
    dataset_dir = os.path.join(DATASET_DIR, dataset)
    uuid2papers = get_airqa_paper_metadata(dataset_dir=dataset_dir)

    # Get the paper info
    paper_info = uuid2papers.get(pdf_id)
    if not paper_info:
        raise ValueError(f"PDF ID '{pdf_id}' not found in the mapping file.")
    
    # Get the PDF path from the mapping
    pdf_path = paper_info.get("pdf_path")
    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    return pdf_path


def build_bm25_corpus(
        paper_dir: str = os.path.join(DATASET_DIR, 'airqa', 'papers'),
        save_path: str = os.path.join(VECTORSTORE_DIR, 'ai_research', 'bm25.json')
):
    """ Build the BM25 corpus for the vectorstore. Indeed, generate the backup JSON file for BM25 model.
    Directly load the PDF files and extract the text content for all preprocessed papers.
    """
    from utils.functions.pdf_functions import get_pdf_page_text
    documents = []
    for conference_dir in os.listdir(paper_dir):
        conference_dir = os.path.join(paper_dir, conference_dir)
        if not os.path.isdir(conference_dir): continue
        logger.info(f"Processing conference directory: {conference_dir}")
        for paper_file in tqdm.tqdm(os.listdir(conference_dir), disable=not sys.stdout.isatty()):
            if not paper_file.endswith('.pdf'): continue
            paper_file = os.path.join(conference_dir, paper_file)
            try:
                # just ignore errors like:
                # MuPDF error: unsupported error: cannot create appearance stream for Screen annotations
                # MuPDF error: syntax error: unknown keyword: 'literal'
                pieces = get_pdf_page_text(paper_file, generate_uuid=False)['page_contents']
            except Exception as e:
                logger.error(f"Failed to extract text from PDF: {paper_file}")
                continue
            documents.extend(pieces)

    embedder: BaseEmbeddingFunction = get_milvus_embedding_function('bm25', 'en')
    embedder.fit(documents)
    embedder.save(save_path)
    return save_path


def encode_database_content(
        vs_conn: MilvusClient,
        db_conn: duckdb.DuckDBPyConnection,
        vs_schema: VectorstoreSchema,
        db_schema: DatabaseSchema,
        pdf_ids: List[str] = [],
        target_collections: List[str] = [],
        batch_size: int = 128,
        on_conflict: str = 'ignore',
        verbose: bool = False
    ) -> None:
    """ Encode the database content into vectors and insert them into the vectorstore.
    @args:
        vs_conn: MilvusClient, the connection to the vectorstore
        db_conn: DatabasePopulation, the connection to the relational database
        vs_schema: VectorstoreSchema, the schema of the vectorstore
        db_schema: DatabaseSchema, the schema of the relational database
        pdf_ids: Optional[Union[List[str], str]], the PDF id or id list to encode, if None, encode all records in the DB
        target_collections: List[str], the collections to encode, if empty, encode all collections in the vectorstore
        batch_size: int, the batch size for encoding
        on_conflict: str, the conflict resolution strategy based on pdf_id, chosen from ['ignore', 'replace', 'raise']
        verbose: bool, whether to print the encoding process or not
    """
    target_collections = vs_schema.collections if not target_collections else target_collections
    if not pdf_ids: # by default, encode all PDFs in the database if not specified
        if verbose: logger.info("[Warning]: `pdf_ids` not specified, encoding all PDFs in the database ...")
        metatable = db_schema.get_metadata_table_name()
        pdf_field = db_schema.get_pdf_and_page_fields(metatable)[0]
        try:
            all_pdf_ids = db_conn.execute(f"SELECT DISTINCT {pdf_field} FROM {metatable}")
            pdf_ids = [str(row[0]) for row in all_pdf_ids]
        except Exception as e:
            logger.error(f"[Error]: Failed to get all PDF ids from the database: {e}")
            return
    if type(pdf_ids) != list: pdf_ids = [pdf_ids]

    for start_idx in tqdm.tqdm(range(0, len(pdf_ids), batch_size), disable=not sys.stdout.isatty()):
        start_time = time.time()
        if logger: logger.info(f"Encoding PDFs from [{start_idx}/{len(pdf_ids)}] ...")
        original_batch_pdf_ids = pdf_ids[start_idx:start_idx + batch_size]

        for cid, collection_name in enumerate(target_collections):
            if verbose: logger.info(f"[{cid}]: Encoding collection {collection_name}...")

            # get info of each collection
            collection: VectorstoreCollection = vs_schema.get_collection(collection_name)
            modality, et, em = collection.modality, collection.embed_type, collection.embed_model
            backup_json = os.path.join(VECTORSTORE_DIR, db_schema.database_name, 'bm25.json') if et == 'bm25' else None
            embedder: BaseEmbeddingFunction = get_milvus_embedding_function(et, em, backup_json=backup_json)
            
            # check conflict if batch_pdf_id is specified, i.e., whether PDF content has been encoded or not
            batch_pdf_ids = check_vectorstore_conflict(vs_conn, collection_name, original_batch_pdf_ids, on_conflict=on_conflict)
            
            if not batch_pdf_ids: continue

            if modality == 'image' and hasattr(embedder, 'cache_pdf_to_images'):
                embedder.cache_pdf_to_images(
                    batch_pdf_ids,
                    [
                        get_image_or_pdf_path(db_schema.database_name, pid)
                        for pid in batch_pdf_ids
                    ]
                )

            # get the records to encode
            for table_name in db_schema.tables:
                primary_keys = db_schema.get_primary_keys(table_name)
                pdf_id_field, page_id_field = db_schema.get_pdf_and_page_fields(table_name)
                assert pdf_id_field is not None, f"PDF id field not found in table {table_name}."

                for column_name in db_schema.table2column(table_name):
                    # only encode encodable columns
                    if not db_schema.is_encodable(table_name, column_name, modality): continue

                    if verbose: logger.info(f"Extract cell values for table=`{table_name}`, column=`{column_name}` ...")
                    if len(batch_pdf_ids) > 1:
                        batch_pdf_id_str = ', '.join([f"'{str(pid)}'" for pid in batch_pdf_ids])
                        where_condition = f"{pdf_id_field} IN ({batch_pdf_id_str})"
                    else:
                        where_condition = f"{pdf_id_field} = \'{str(batch_pdf_ids[0])}\'"
                    result = retrieve_cell_values(
                        db_conn, table_name, column_name, primary_keys,
                        pdf_and_page_fields=(pdf_id_field, page_id_field),
                        where_condition=where_condition
                    )

                    # post-process each record
                    if len(result) == 0:
                        if logger: logger.warning(f"No records found for table=`{table_name}`, column=`{column_name}` among PDF ids: {batch_pdf_ids}.")
                        continue
                    documents, records = [], []
                    for row in result:
                        record = {'table_name': table_name, 'column_name': column_name, 'pdf_id': '', 'page_number': -1, 'primary_key': ''}
                        if page_id_field is not None:
                            pdf_id, page_id = str(row[1]), str(row[2])
                            record['pdf_id'] = pdf_id
                            record['page_number'] = get_page_number_from_id(db_schema.database_name, db_conn, pdf_id, page_id)
                            record['primary_key'] = ','.join([str(v) for v in row[3:]])
                        else: # page_id field is None
                            record['pdf_id'] = str(row[1])
                            record['primary_key'] = ','.join([str(v) for v in row[2:]])

                        if modality == 'text': # fill in 'text' field
                            text = str(row[0]).strip()
                            if text in ['', 'None']: continue
                            # do not save the text field for the sake of space, sacrificing time
                            # record['text'] = text
                            documents.append(text)
                            records.append(record)
                        else: # fill in 'bbox' field
                            try:
                                bbox = list(row[0])
                                assert len(bbox) == 4
                                bbox = [math.floor(bbox[0]), math.floor(bbox[1]), math.ceil(bbox[2]), math.ceil(bbox[3])]
                            except Exception as e: continue
                            record['bbox'] = bbox
                            image_or_pdf_path = get_image_or_pdf_path(db_schema.database_name, record['pdf_id'])
                            documents.append({"path": image_or_pdf_path, "page": record['page_number'], "bbox": bbox})
                            records.append(record)

                    # encode the records
                    if len(records) == 0: continue
                    if verbose: logger.info(f"Encode {len(records)} records into vectors with {et} model: {em} ...")
                    vectors: Union[csr_array, List[np.ndarray]] = embedder.encode_documents(documents)
                    if verbose: logger.info(f"Insert {len(records)} records into collection {collection_name} ...")
                    for i, record in enumerate(records):
                        record['vector'] = vectors[i] if et not in ['splade', 'bm25'] else vectors[i:i+1, :]
                    vs_conn.insert(collection_name=collection_name, data=records)

            if modality == 'image' and hasattr(embedder, 'clear_cache'):
                embedder.clear_cache()
        if verbose: logger.info(f'Finished encoding {len(original_batch_pdf_ids)} PDFs, costing {time.time() - start_time}s .')
    return


def check_vectorstore_conflict(
        vs_conn: MilvusClient,
        collection_name: str,
        pdf_ids: Optional[Union[str, List[str]]],
        on_conflict: str = 'ignore',
        pdf_field: str = 'pdf_id',
        batch_size: int = 128
    ) -> List[str]:
    if not pdf_ids: return pdf_ids
    if type(pdf_ids) == str: pdf_ids = [pdf_ids]

    def search_each_pdf_id(batch_ids: List[str]) -> List[str]:
        tmp_pdf_ids = []
        for pid in batch_ids:
            filter_condition = f"{pdf_field} == '{str(pid)}'"
            result = vs_conn.query(collection_name=collection_name, filter=filter_condition, limit=1)
            if len(result) == 0:
                tmp_pdf_ids.append(pid)
        return tmp_pdf_ids

    output_pdfs = []
    for start_idx in range(0, len(pdf_ids), batch_size):
        batch_ids = pdf_ids[start_idx:start_idx + batch_size]
        batch_pdf_id_str = ', '.join([f"'{str(pid)}'" for pid in batch_ids])
        filter_condition = f"{pdf_field} in [{batch_pdf_id_str}]"
        if on_conflict == 'replace':
            vs_conn.delete(collection_name=collection_name, filter=filter_condition)
        elif on_conflict == 'ignore':
            result = vs_conn.query(collection_name=collection_name, filter=filter_condition, limit=1)
            batch_ids = search_each_pdf_id(batch_ids) if len(result) > 0 else batch_ids
            output_pdfs.extend(batch_ids)
        else:
            result = vs_conn.query(collection_name=collection_name, filter=filter_condition, limit=1)
            if len(result) > 0:
                raise ValueError(f"PDF id(s) in {search_each_pdf_id(batch_ids)} already exist in collection {collection_name}.")
    return pdf_ids if on_conflict != 'ignore' else output_pdfs


def get_pdf_ids_to_encode(pdf_path_or_id: str) -> List[str]:
    """ Get the list of valid PDF ids to encode.
    @args:
        pdf_path_or_id: str, the path to the PDF file or the PDF id [list].
    @return:
        pdf_ids: List[str], the list of valid PDF ids
    """
    if os.path.exists(pdf_path_or_id):
        with open(pdf_path_or_id, mode='r', encoding='utf8') as f:
            if pdf_path_or_id.endswith('.json'):
                pdf_ids = json.load(f)
            else:
                pdf_ids = f.readlines()
                pdf_ids = [pdf_id.strip() for pdf_id in pdf_ids if pdf_id.strip()]
        return pdf_ids
    else:
        from utils.functions import is_valid_uuid
        if type(pdf_path_or_id) == str:
            if is_valid_uuid(pdf_path_or_id):
                return [pdf_path_or_id]
            elif ',' in pdf_path_or_id:
                pdf_ids = pdf_path_or_id.split(',')
                return get_pdf_ids_to_encode(pdf_ids)
            else:
                raise ValueError(f"Invalid PDF path or PDF id: {pdf_path_or_id}.")
        elif type(pdf_path_or_id) in [list, tuple]:
            for pdf_id in pdf_path_or_id:
                if not is_valid_uuid(pdf_id):
                    raise ValueError(f"Invalid PDF id: {pdf_id}.")
            return list(pdf_path_or_id)
        else:
            raise ValueError(f"Invalid PDF path or PDF id: {pdf_path_or_id}.")


if __name__ == '__main__':

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--vectorstore', type=str, help='which vectorstore, indeed the database name.')
    parser.add_argument('--launch_method', type=str, default='standalone', help='launch method for vectorstore, chosen from ["docker", "standalone"].')
    parser.add_argument('--docker_uri', type=str, default='http://127.0.0.1:19530', help='host + port for milvus started from docker')
    parser.add_argument('--vectorstore_path', type=str, help='Path to the vectorstore if launch_method is "standalone".')
    parser.add_argument('--database_path', type=str, help='Path to the relational database.')
    parser.add_argument('--pdf_path', type=str, help='File containing the list of PDF ids to encode (pls. ensure they exist in the relational database)')
    parser.add_argument('--target_collections', type=str, nargs='*', help='Target collections to encode content into.')
    parser.add_argument('--batch_size', type=int, default=128, help='batch size for pdf content retrieval')
    parser.add_argument('--on_conflict', type=str, default='ignore', choices=['replace', 'ignore', 'raise'], help='how to handle the database content insertion conflict (a.k.a., the PDF has been processed)')
    parser.add_argument('--from_scratch', action='store_true', help='remove the existed vectorstore or not')
    args = parser.parse_args()

    # the schema of relational database records which column will be encoded into vectors
    db_conn: duckdb.DuckDBPyConnection = get_database_connection(args.vectorstore, database_path=args.database_path)
    db_schema: DatabaseSchema = DatabaseSchema(args.vectorstore)
    vs_conn: MilvusClient = get_vectorstore_connection(args.vectorstore, launch_method=args.launch_method, docker_uri=args.docker_uri, vectorstore_path=args.vectorstore_path, from_scratch=args.from_scratch)
    vs_schema: VectorstoreSchema = VectorstoreSchema(args.vectorstore)

    # initialize the vectorstore schema
    initialize_vectorstore(vs_conn, vs_schema)

    # pdf ids to encode, by default, all pdfs in the relational database if not specified
    pdf_ids = get_pdf_ids_to_encode(args.pdf_path) if args.pdf_path else []
    encode_database_content(
        vs_conn, db_conn, vs_schema, db_schema,
        pdf_ids=pdf_ids, target_collections=args.target_collections,
        batch_size=args.batch_size, on_conflict=args.on_conflict, verbose=True
    )

    db_conn.close()
    vs_conn.close()
