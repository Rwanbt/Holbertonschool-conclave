import { calculateActiveStep, FLOW_STEPS } from '../steps'
import type { AnalysisEvent, AnalysisSnapshot } from '../types'

interface ConclaveStepperProps {
  snapshot: AnalysisSnapshot | null
  events: readonly AnalysisEvent[]
}

export function ConclaveStepper({ snapshot, events }: ConclaveStepperProps) {
  const activeStep = calculateActiveStep(snapshot, events)

  return (
    <ol className="stepper" aria-label="Progression de l'analyse">
      {FLOW_STEPS.map((step, index) => {
        const state = index < activeStep ? 'done' : index === activeStep ? 'active' : 'todo'
        return (
          <li
            key={step.id}
            className={`stepper-step stepper-step--${state}`}
            aria-current={state === 'active' ? 'step' : undefined}
          >
            <span className="stepper-index">{index + 1}</span>
            <span className="stepper-body">
              <span className="stepper-label">{step.label}</span>
              <span className="stepper-description">{step.description}</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}