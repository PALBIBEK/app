/** Memory lives on one page now: notebooks by harness and version. */
import { redirect } from 'next/navigation';

export default function MemoryRedirect(): never {
  redirect('/memory');
}
