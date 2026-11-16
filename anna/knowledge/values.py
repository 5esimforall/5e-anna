"""Source evidence and sentence boundaries shared by knowledge modules."""
import re


def evidence(path, text, start, end):
    return {'path': path, 'span': [start, end], 'raw': text[start:end]}


def sentences(text, start, end):
    for m in re.finditer(r'[^。！？]+[。！？]?', text[start:end]):
        a, b = start+m.start(), start+m.end()
        while a < b and text[a].isspace(): a += 1
        while b > a and text[b-1].isspace(): b -= 1
        if a < b: yield a, b, re.sub(r'\s+', '', text[a:b])
