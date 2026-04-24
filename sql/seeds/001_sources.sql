-- Stage 1 seed: upstream source registry
-- Idempotent by unique constraint on sources.name

INSERT INTO sources (name, url, family, is_active, created_at, updated_at)
VALUES
  (
    'BLACK_VLESS_RUS',
    'https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_VLESS_RUS.txt',
    'black',
    TRUE,
    now(),
    now()
  ),
  (
    'WHITE_CIDR_RU_ALL',
    'https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/WHITE-CIDR-RU-all.txt',
    'white_cidr',
    TRUE,
    now(),
    now()
  ),
  (
    'WHITE_SNI_RU_ALL',
    'https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/WHITE-SNI-RU-all.txt',
    'white_sni',
    TRUE,
    now(),
    now()
  )
ON CONFLICT (name)
DO UPDATE SET
  url = EXCLUDED.url,
  family = EXCLUDED.family,
  is_active = EXCLUDED.is_active,
  updated_at = now();
