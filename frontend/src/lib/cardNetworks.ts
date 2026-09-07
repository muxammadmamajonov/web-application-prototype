/**
 * Card networks and their scheme styling — the client-side mirror of
 * `backend/app/core/card_networks.py`.
 *
 * The network is DECLARED, never inferred from the number: Uzcard issues in
 * the 5614 range, inside Mastercard's 51-55 range, so the same digits are two
 * networks with two settlement currencies.
 *
 * Display and the operator's picker only — the server re-derives and
 * re-validates on every save and every submission, so this file cannot widen
 * what the platform accepts.
 */
import humoLogo from '@/assets/card-schemes/humo.png';
import uzcardLogo from '@/assets/card-schemes/uzcard.png';
import visaLogo from '@/assets/card-schemes/visa.png';
import unionpayLogo from '@/assets/card-schemes/unionpay.png';
import mastercardLogo from '@/assets/card-schemes/mastercard.png';
import mirLogo from '@/assets/card-schemes/mir.png';
import maestroLogo from '@/assets/card-schemes/maestro.png';

/** The networks the platform can settle a top-up on. */
export const CARD_NETWORKS = ['UZCARD', 'HUMO', 'VISA', 'MASTERCARD'] as const;
export type CardNetwork = (typeof CARD_NETWORKS)[number];

/** The settlement currency each network receives. Display only — the server
 *  derives and stores the authoritative value from the same matrix. */
export const NETWORK_CURRENCY: Record<CardNetwork, string> = {
  UZCARD: 'UZS', HUMO: 'UZS', VISA: 'USD', MASTERCARD: 'USD',
};

export interface SchemeStyle {
  bg: string;        // card background (CSS gradient)
  light?: boolean;   // light card → use dark text
  logo?: string;     // network logo, bundled internally
  logoH: number;     // rendered logo height in px
}

// Each scheme gets a distinct, brand-appropriate background chosen to make its
// (internally bundled) logo read clearly — no white chips.
export const SCHEME_STYLES: Record<string, SchemeStyle> = {
  uzcard:     { bg: 'linear-gradient(135deg,#eef1f7 0%,#cfd8ea 55%,#aebbd8 100%)', light: true, logo: uzcardLogo, logoH: 40 },
  humo:       { bg: 'linear-gradient(135deg,#0fa192 0%,#0a6c64 55%,#063f3a 100%)', logo: humoLogo, logoH: 26 },
  visa:       { bg: 'linear-gradient(135deg,#243b8f 0%,#16265f 55%,#0a1640 100%)', logo: visaLogo, logoH: 26 },
  mastercard: { bg: 'linear-gradient(135deg,#34353b 0%,#1a1b1f 60%,#050507 100%)', logo: mastercardLogo, logoH: 30 },
  unionpay:   { bg: 'linear-gradient(135deg,#2a416f 0%,#1a2a50 55%,#0d1730 100%)', logo: unionpayLogo, logoH: 26 },
  mir:        { bg: 'linear-gradient(135deg,#33414f 0%,#1d2733 55%,#0e151e 100%)', logo: mirLogo, logoH: 22 },
  maestro:    { bg: 'linear-gradient(135deg,#2c3340 0%,#181c24 60%,#0a0d12 100%)', logo: maestroLogo, logoH: 30 },
  default:    { bg: 'linear-gradient(135deg,#3a3f47 0%,#23262b 100%)', logoH: 26 },
};
