/** Types shared by more than one persona's queries. */

export interface OverageRule { price: number; enabled: boolean; mode: 'soft' | 'hard' }
