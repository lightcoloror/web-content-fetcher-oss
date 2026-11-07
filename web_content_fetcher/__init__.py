from .batch import BatchFetchRequest, fetch_batch, read_url_list
from .core import FetchRequest, FetchResult, fetch_content
from .document_router import DocumentRoute, classify_document_url, fetch_document_link
from .archivebox_backend import ArchiveBoxRequest, ArchiveBoxResult, run_archivebox_add
from .zsxq_cli import ZsxqRequest, ZsxqResult, run_zsxq
from .wechat_search import WechatSearchArchiveRequest, search_and_archive_wechat

__all__ = [
    "DocumentRoute",
    "ArchiveBoxRequest",
    "ArchiveBoxResult",
    "BatchFetchRequest",
    "FetchRequest",
    "FetchResult",
    "ZsxqRequest",
    "ZsxqResult",
    "WechatSearchArchiveRequest",
    "classify_document_url",
    "fetch_batch",
    "fetch_content",
    "fetch_document_link",
    "read_url_list",
    "run_archivebox_add",
    "run_zsxq",
    "search_and_archive_wechat",
]
