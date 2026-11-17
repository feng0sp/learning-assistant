"""Original PDF text and bookmarks used to test extraction and chapter boundaries."""
import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

PAGE_TEXTS = [
    'Chapter one begins. The network layer delivers packets across networks.',
    'Forwarding section begins. A router chooses the next hop using destination addresses.',
    'Forwarding section continues. The longest matching prefix determines the selected route.',
    'Chapter two begins. The transport layer serves communication between application processes.',
]


def bookmarked_pdf(irregular=False, untrusted_title=False):
    writer = PdfWriter()
    for text in PAGE_TEXTS:
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 20 760 Td ({text}) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    if irregular:
        writer.add_outline_item('先列出后面的章节', 3)
        writer.add_outline_item('再跳回前面的章节', 0)
    else:
        parent = writer.add_outline_item('第一章 网络层', 0)
        writer.add_outline_item('1.1 网络层概览', 0, parent=parent)
        writer.add_outline_item('1.2 分组转发', 1, parent=parent)
        last_title = '<img src=x onerror="window.__outlineInjected=1">' if untrusted_title else '第二章 传输层'
        writer.add_outline_item(last_title, 3)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
