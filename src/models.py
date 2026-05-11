from dataclasses import dataclass


@dataclass
class Transcript:
    ticker: str
    company_name: str
    quarter: str
    year: int
    date: str
    url: str
    text: str
