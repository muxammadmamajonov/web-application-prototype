import { AppRouter } from './routes/router';
import { FeedbackHost } from './lib/feedback';
import { useTelegramBoot } from './lib/telegram/boot';

export default function App() {
  // Initialise the Telegram Mini App once (theme + native chrome + safe-area /
  // viewport CSS vars). Inert outside Telegram.
  useTelegramBoot();
  return (
    <>
      <AppRouter />
      <FeedbackHost />
    </>
  );
}
