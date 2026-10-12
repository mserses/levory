import styles from '../NewRequestPage.module.css';

/** Parola uyarısı: formun en üstünde, göze çarpan ama sakin bir not. */
export function SafetyNotice() {
  return (
    <div className={styles.notice} role="note" aria-labelledby="talep-parola-uyari">
      <p id="talep-parola-uyari" className={styles.noticeTitle}>
        Parolanızı asla yazmayın
      </p>
      <p>
        Destek ekibi parolanızı, doğrulama kodunuzu veya PIN’inizi hiçbir zaman istemez. Bu
        bilgileri başlığa, açıklamaya ya da ekran görüntülerine eklemeyin.
      </p>
    </div>
  );
}
