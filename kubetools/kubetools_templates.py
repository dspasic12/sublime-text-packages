# Minimal starter YAML for .kubetools - Create… (no Sublime import).
# Apply is blocked while metadata.namespace is CHANGE-ME or name is example.

TEMPLATE_ORDER = (
    "deployment",
    "statefulset",
    "daemonset",
    "job",
    "cronjob",
    "service",
    "ingress",
    "configmap",
    "secret",
    "pvc",
    "serviceaccount",
    "hpa",
    "networkpolicy",
)

TEMPLATES = {
    "deployment": {
        "caption": "Deployment",
        "detail": "apps/v1 — set namespace (CHANGE-ME) and name before Apply",
        "kind_label": "Deployment",
        "body": """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  selector:
    matchLabels:
      app: example
  template:
    metadata:
      labels:
        app: example
    spec:
      containers:
        - name: example
          image: nginx:1.27-alpine
""",
    },
    "statefulset": {
        "caption": "StatefulSet",
        "detail": "apps/v1 — needs a matching Service name",
        "kind_label": "StatefulSet",
        "body": """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  serviceName: example
  selector:
    matchLabels:
      app: example
  template:
    metadata:
      labels:
        app: example
    spec:
      containers:
        - name: example
          image: nginx:1.27-alpine
""",
    },
    "daemonset": {
        "caption": "DaemonSet",
        "detail": "apps/v1 — one pod per node",
        "kind_label": "DaemonSet",
        "body": """\
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  selector:
    matchLabels:
      app: example
  template:
    metadata:
      labels:
        app: example
    spec:
      containers:
        - name: example
          image: nginx:1.27-alpine
""",
    },
    "job": {
        "caption": "Job",
        "detail": "batch/v1 — run-to-completion",
        "kind_label": "Job",
        "body": """\
apiVersion: batch/v1
kind: Job
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  backoffLimit: 1
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: example
          image: busybox:1.36
          command: ["sh", "-c", "echo ok"]
""",
    },
    "cronjob": {
        "caption": "CronJob",
        "detail": "batch/v1 — scheduled Job",
        "kind_label": "CronJob",
        "body": """\
apiVersion: batch/v1
kind: CronJob
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  schedule: "0 * * * *"
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: example
              image: busybox:1.36
              command: ["sh", "-c", "echo ok"]
""",
    },
    "service": {
        "caption": "Service",
        "detail": "v1 ClusterIP — selector app: example",
        "kind_label": "Service",
        "body": """\
apiVersion: v1
kind: Service
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  selector:
    app: example
  ports:
    - name: http
      port: 80
      targetPort: 80
""",
    },
    "ingress": {
        "caption": "Ingress",
        "detail": "networking.k8s.io/v1 — host example.local",
        "kind_label": "Ingress",
        "body": """\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  ingressClassName: nginx
  rules:
    - host: example.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: example
                port:
                  number: 80
""",
    },
    "configmap": {
        "caption": "ConfigMap",
        "detail": "v1 — keys in data",
        "kind_label": "ConfigMap",
        "body": """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: example
  namespace: CHANGE-ME
data:
  example.conf: |
    # replace
""",
    },
    "secret": {
        "caption": "Secret",
        "detail": "v1 Opaque — change keys; Seal instead of Apply if needed",
        "kind_label": "Secret",
        "body": """\
apiVersion: v1
kind: Secret
metadata:
  name: example-secrets
  namespace: CHANGE-ME
type: Opaque
stringData:
  EXAMPLE_KEY: "changeme"
""",
    },
    "pvc": {
        "caption": "PersistentVolumeClaim",
        "detail": "v1 — 1Gi RWO; cluster default StorageClass",
        "kind_label": "PersistentVolumeClaim",
        "body": """\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
""",
    },
    "serviceaccount": {
        "caption": "ServiceAccount",
        "detail": "v1",
        "kind_label": "ServiceAccount",
        "body": """\
apiVersion: v1
kind: ServiceAccount
metadata:
  name: example
  namespace: CHANGE-ME
""",
    },
    "hpa": {
        "caption": "HorizontalPodAutoscaler",
        "detail": "autoscaling/v2 — targets Deployment/example",
        "kind_label": "HorizontalPodAutoscaler",
        "body": """\
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: example
  minReplicas: 1
  maxReplicas: 3
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
""",
    },
    "networkpolicy": {
        "caption": "NetworkPolicy",
        "detail": "networking.k8s.io/v1 — allow from same app label",
        "kind_label": "NetworkPolicy",
        "body": """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  podSelector:
    matchLabels:
      app: example
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: example
      ports:
        - protocol: TCP
          port: 80
""",
    },
}
