import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import type { Incident } from '@/api';
import { Button, buttonClassName } from '@/components';
import { formatDateTime } from '@/lib/format';
import { usePageTitle } from '@/lib/usePageTitle';
import styles from '../NewRequestPage.module.css';

export interface RequestSuccessProps {
  incident: Incident;
  onNewRequest: () => void;
}

/** Talep oluşturulduktan sonra: kayıt numarası + sonraki adımlar (AI triage + insan onayı). */
export function RequestSuccess({ incident, onNewRequest }: RequestSuccessProps) {
  usePageTitle('Talebiniz alındı');
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    // Ekran okuyucu ve klavye kullanıcıları sonucu hemen duysun
    headingRef.current?.focus();
  }, []);

  return (
    <div className="page">
      <section className={`card ${styles.success ?? ''}`} aria-labelledby="talep-basarili">
        <div>
          <h1 id="talep-basarili" ref={headingRef} tabIndex={-1} className={styles.successHeading}>
            Talebiniz alındı
          </h1>
          <p>
            Kayıt numaranız:
            <br />
            <span className={styles.ticketNumber} data-testid="ticket-number">
              {incident.number}
            </span>
          </p>
          <p className="muted">
            “<span className="prewrap">{incident.title}</span>” ·{' '}
            {formatDateTime(incident.created_at)}
          </p>
          <p className="muted">
            Bu numarayı not alın; destek ekibiyle görüşürken kullanabilirsiniz.
          </p>
        </div>

        <div>
          <h2>Bundan sonra ne olacak?</h2>
          <ol className={styles.steps}>
            <li>
              <span>
                <strong>Yapay zekâ ön değerlendirmesi</strong>
                <span className="muted">
                  Asistan talebinizi sınıflandırır, önceliği ve ilgili ekibi önerir. Bu yalnızca bir
                  öneridir.
                </span>
              </span>
            </li>
            <li>
              <span>
                <strong>Destek uzmanı inceler</strong>
                <span className="muted">
                  Önemli kararları her zaman bir insan onaylar; uzman talebinizi üstlenir ve
                  gerekirse sizinle iletişime geçer.
                </span>
              </span>
            </li>
            <li>
              <span>
                <strong>Durumu takip edin</strong>
                <span className="muted">
                  Talebinizin durumunu ve hedef çözüm süresini “Taleplerim” sayfasından
                  izleyebilirsiniz.
                </span>
              </span>
            </li>
          </ol>
        </div>

        <div className="row">
          <Link to="/taleplerim" className={buttonClassName('primary')}>
            Taleplerimi gör
          </Link>
          <Button variant="secondary" onClick={onNewRequest}>
            Yeni talep oluştur
          </Button>
        </div>
      </section>
    </div>
  );
}
