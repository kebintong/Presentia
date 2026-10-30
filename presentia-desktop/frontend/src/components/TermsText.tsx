/**
 * Terms of Use and Privacy Notice shown on first start (Onboarding.tsx).
 *
 * DRAFT written by the developer: have the school's data protection officer
 * review it before release. Change TERMS_VERSION whenever the text changes in
 * substance: every teacher is then asked to accept it again, and the engine
 * records which version they accepted and when (/api/setup).
 */
export const TERMS_VERSION = '2026-10-30'

export default function TermsText() {
  return (
    <div className="terms-text">
      <h3>Terms of Use</h3>
      <p>
        Presentia helps teachers take attendance. It recognises students by face during online classes and in
        person, using face templates made from photos the students or the teacher provide.
      </p>
      <ul>
        <li>
          <strong>Use it for your classes only.</strong> Use Presentia to take attendance for the classes you teach,
          as your school allows. Tell your students that Presentia is used and how, and get their consent where your
          school or the law requires it.
        </li>
        <li>
          <strong>Check the results.</strong> Face recognition can make mistakes. Review attendance before you use
          it for grades or decisions, and correct it where needed.
        </li>
        <li>
          <strong>Keep it safe.</strong> Protect the computer Presentia runs on and, if you use the phone app, your
          account password.
        </li>
        <li>
          <strong>No guarantee.</strong> Presentia is free software provided as it is, without warranty. The people
          who make it are not liable for decisions made with it or for data lost through misuse.
        </li>
        <li>
          <strong>Face models.</strong> The face recognition models Presentia downloads (InsightFace) may only be
          used for non-commercial purposes.
        </li>
        <li>
          <strong>Changes.</strong> If these terms change, Presentia asks you to accept them again.
        </li>
      </ul>

      <h3>Privacy Notice</h3>
      <p><strong>On this computer</strong> Presentia keeps:</p>
      <ul>
        <li>your classes, and each student's name and student number;</li>
        <li>each student's face template: 512 numbers that describe the face, not a photo;</li>
        <li>attendance, alerts and session reports;</li>
        <li>a small photo of each online registration until you accept or decline it.</li>
      </ul>
      <p>
        <strong>The registration website</strong> keeps a student's registration (name, student number, three
        photos) only until Presentia collects it, and deletes anything not collected within 14 days. No face
        template is made or kept there.
      </p>
      <p>
        <strong>The phone app, only if you sign in:</strong> your classes, students with their face templates,
        attendance and waiting registrations are copied to your own Presentia account online (Supabase), which only
        you can open. Deleting something in Presentia deletes it there too. Signing out stops the copying.
      </p>
      <p>
        <strong>Diagnostic reports</strong> are sent only when you press Send, after you have seen what they
        contain, and are kept for 30 days.
      </p>
      <p>
        Presentia does not sell or share data, shows no ads and does not track how you use it. Its only purpose is
        attendance.
      </p>
      <p>
        <strong>Your choices and rights.</strong> You can remove a student or a class at any time, or delete
        everything in Settings → Data. Under the Data Privacy Act of 2012 (Republic Act No. 10173), students may ask
        to access, correct or delete their information: they should contact their teacher or the school's data
        protection officer.
      </p>
    </div>
  )
}
