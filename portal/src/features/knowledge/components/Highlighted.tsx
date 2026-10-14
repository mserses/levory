import { highlightSegments } from '../highlight';

/** Metni düz metin olarak basar; sorgu eşleşmelerini <mark> ile sarar (innerHTML yok). */
export function Highlighted({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlightSegments(text, query).map((seg, i) =>
        seg.match ? <mark key={i}>{seg.text}</mark> : <span key={i}>{seg.text}</span>,
      )}
    </>
  );
}
