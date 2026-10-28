import com.sun.source.tree.*;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreePathScanner;
import com.sun.source.util.Trees;
import javax.tools.*;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Syntax-only Java 8 AST evidence: candidates, not type-resolved call bindings. */
public final class JavaLogicAst {
    private static String compact(String text) {
        return text.replaceAll("\\s+", " ").trim();
    }
    private static String encoded(String text) {
        return Base64.getEncoder().encodeToString(text.getBytes(StandardCharsets.UTF_8));
    }
    private static String digest(String text) {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256").digest(compact(text).getBytes(StandardCharsets.UTF_8));
            StringBuilder output = new StringBuilder();
            for (byte b : bytes) output.append(String.format("%02x", b & 255));
            return output.toString();
        } catch (Exception e) { throw new IllegalStateException(e); }
    }
    private static String methodName(ExpressionTree method) {
        if (method instanceof MemberSelectTree) return ((MemberSelectTree) method).getIdentifier().toString();
        if (method instanceof IdentifierTree) return ((IdentifierTree) method).getName().toString();
        return method.toString();
    }
    private static String classifyCall(String name) {
        String n = name.toLowerCase(Locale.ROOT);
        if (n.matches(".*(trylock|unlock|lock|setnx|acquirelock|releaselock).*")) return "CONCURRENCY";
        if (n.matches(".*(rollback|commit|begintxn|begintransaction|setautocommit).*")) return "TRANSACTION";
        if (n.matches(".*(retry|redeliver|resend|requeue|schedulequery|schedulecheck).*")) return "RETRY";
        if (n.matches(".*(getproperty|getparameter|getconfig|getboolean|getint|getstring|getvalue|isenabled).*")) return "CONFIG";
        if (n.matches("^(set|update|mark|change)(.*)(status|state|result|success|fail|paid|refund)(.*)$") ||
            n.matches("^(setstatus|setstate|setresult|markpaid|markfailed)$")) return "STATE";
        return null;
    }
    private static boolean statusName(String variable) {
        return variable.toLowerCase(Locale.ROOT).matches(".*(status|state|result|paid|refundstate|refundstatus).*\\b");
    }
    private static class Scan extends TreePathScanner<Void, Void> {
        private final CompilationUnitTree unit;
        private final SourcePositions positions;
        private final Deque<String> classes = new ArrayDeque<String>();
        private String member = "<initializer>";
        Scan(CompilationUnitTree unit, Trees trees) {
            this.unit = unit;
            this.positions = trees.getSourcePositions();
        }
        private void emit(String category, String detail, Tree node) {
            long pos = positions.getStartPosition(unit, node);
            long line = pos < 0 ? -1 : unit.getLineMap().getLineNumber(pos);
            String scope = String.join(".", classes) + "#" + member;
            System.out.println(encoded(scope) + "\t" + category + "\t" +
                encoded(compact(detail)) + "\t" + digest(node.toString()) + "\t" + line);
        }
        @Override public Void visitClass(ClassTree node, Void unused) {
            classes.addLast(node.getSimpleName().toString());
            super.visitClass(node, unused);
            classes.removeLast();
            return null;
        }
        @Override public Void visitMethod(MethodTree node, Void unused) {
            String previous = member;
            StringBuilder signature = new StringBuilder(node.getName().toString()).append('(');
            for (VariableTree argument : node.getParameters()) {
                if (signature.charAt(signature.length()-1) != '(') signature.append(',');
                signature.append(compact(argument.getType().toString()));
            }
            member = signature.append(')').toString();
            super.visitMethod(node, unused);
            member = previous;
            return null;
        }
        @Override public Void visitIf(IfTree node, Void unused) {
            emit("BRANCH", "if " + node.getCondition().toString(), node);
            return super.visitIf(node, unused);
        }
        @Override public Void visitSwitch(SwitchTree node, Void unused) {
            emit("BRANCH", "switch " + node.getExpression().toString(), node);
            return super.visitSwitch(node, unused);
        }
        @Override public Void visitConditionalExpression(ConditionalExpressionTree node, Void unused) {
            emit("BRANCH", "ternary " + node.getCondition().toString(), node);
            return super.visitConditionalExpression(node, unused);
        }
        @Override public Void visitCatch(CatchTree node, Void unused) {
            emit("EXCEPTION", "catch " + node.getParameter().getType().toString(), node);
            return super.visitCatch(node, unused);
        }
        @Override public Void visitThrow(ThrowTree node, Void unused) {
            emit("EXCEPTION", "throw " + node.getExpression().getKind(), node);
            return super.visitThrow(node, unused);
        }
        @Override public Void visitTry(TryTree node, Void unused) {
            if (node.getFinallyBlock() != null) emit("EXCEPTION", "finally", node.getFinallyBlock());
            return super.visitTry(node, unused);
        }
        @Override public Void visitSynchronized(SynchronizedTree node, Void unused) {
            emit("CONCURRENCY", "synchronized " + node.getExpression().toString(), node);
            return super.visitSynchronized(node, unused);
        }
        @Override public Void visitMethodInvocation(MethodInvocationTree node, Void unused) {
            String name = methodName(node.getMethodSelect());
            String category = classifyCall(name);
            if (category != null) emit(category, "call " + name, node);
            return super.visitMethodInvocation(node, unused);
        }
        @Override public Void visitAssignment(AssignmentTree node, Void unused) {
            if (statusName(node.getVariable().toString()))
                emit("STATE", "assignment " + node.getVariable().toString(), node);
            return super.visitAssignment(node, unused);
        }
        @Override public Void visitVariable(VariableTree node, Void unused) {
            if (node.getInitializer() != null && statusName(node.getName().toString()))
                emit("STATE", "initialization " + node.getName().toString(), node);
            return super.visitVariable(node, unused);
        }
        @Override public Void visitAnnotation(AnnotationTree node, Void unused) {
            String kind = node.getAnnotationType().toString();
            if (kind.endsWith("Transactional") || kind.endsWith("TransactionAttribute"))
                emit("TRANSACTION", "annotation " + kind, node);
            else if (kind.endsWith("Value") || kind.endsWith("ConfigurationProperties"))
                emit("CONFIG", "annotation " + kind, node);
            return super.visitAnnotation(node, unused);
        }
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("Expected one Java source file");
            System.exit(2);
        }
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) throw new IllegalStateException("A JDK, not a JRE, is required");
        DiagnosticCollector<JavaFileObject> diag = new DiagnosticCollector<JavaFileObject>();
        StandardJavaFileManager manager = compiler.getStandardFileManager(diag, Locale.ROOT, StandardCharsets.UTF_8);
        try {
            Iterable<? extends JavaFileObject> files = manager.getJavaFileObjects(new File(args[0]));
            JavacTask task = (JavacTask) compiler.getTask(null, manager, diag,
                Arrays.asList("-source", "8", "-proc:none"), null, files);
            Iterable<? extends CompilationUnitTree> units = task.parse();
            for (Diagnostic<? extends JavaFileObject> d : diag.getDiagnostics()) {
                if (d.getKind() == Diagnostic.Kind.ERROR) {
                    System.err.println("Source parse failed at line " + d.getLineNumber());
                    System.exit(3);
                }
            }
            Trees trees = Trees.instance(task);
            for (CompilationUnitTree unit : units) new Scan(unit, trees).scan(unit, null);
        } finally { manager.close(); }
    }
}
