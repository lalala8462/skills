import com.sun.source.tree.*;
import com.sun.source.util.*;
import javax.tools.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** 只解析源码，不加载目标项目依赖或运行注解处理器。 */
public final class JavaSourceIndex {
    static String json(Object v) {
        if (v == null) return "null";
        if (v instanceof Number || v instanceof Boolean) return v.toString();
        if (v instanceof Map) {
            List<String> a = new ArrayList<String>();
            for (Object k : ((Map<?, ?>) v).keySet()) a.add(json(k) + ":" + json(((Map<?, ?>) v).get(k)));
            return "{" + String.join(",", a) + "}";
        }
        if (v instanceof Iterable) {
            List<String> a = new ArrayList<String>();
            for (Object x : (Iterable<?>) v) a.add(json(x));
            return "[" + String.join(",", a) + "]";
        }
        StringBuilder s = new StringBuilder("\"");
        for (char c : v.toString().toCharArray()) {
            switch (c) {
                case '"': s.append("\\\""); break;
                case '\\': s.append("\\\\"); break;
                case '\n': s.append("\\n"); break;
                case '\r': s.append("\\r"); break;
                case '\t': s.append("\\t"); break;
                default: if (c < 32) s.append(String.format("\\u%04x", (int)c)); else s.append(c);
            }
        }
        return s.append('"').toString();
    }
    static Map<String, Object> map(Object... a) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        for (int i = 0; i < a.length; i += 2) m.put((String)a[i], a[i + 1]);
        return m;
    }
    static List<Object> annotations(ModifiersTree node) {
        List<Object> result = new ArrayList<Object>();
        for (AnnotationTree a : node.getAnnotations()) {
            Map<String, Object> values = new LinkedHashMap<String, Object>();
            for (ExpressionTree arg : a.getArguments()) {
                if (arg instanceof AssignmentTree) {
                    AssignmentTree assign = (AssignmentTree)arg;
                    values.put(assign.getVariable().toString(), assign.getExpression().toString());
                } else values.put("value", arg.toString());
            }
            result.add(map("name", a.getAnnotationType().toString(), "arguments", values));
        }
        return result;
    }
    static class Scan extends TreePathScanner<Void, Void> {
        final CompilationUnitTree unit;
        final SourcePositions positions;
        final String source;
        final String pkg;
        final List<String> imports = new ArrayList<String>();
        final Deque<String> owners = new ArrayDeque<String>();
        final Map<String, Integer> anonymous = new HashMap<String, Integer>();
        String method = null;
        Scan(CompilationUnitTree u, Trees trees, String text) {
            unit = u; positions = trees.getSourcePositions(); source = text;
            pkg = u.getPackageName() == null ? "" : u.getPackageName().toString();
            for (ImportTree i : u.getImports()) imports.add((i.isStatic() ? "static " : "") + i.getQualifiedIdentifier());
        }
        String owner() { return (pkg.isEmpty() ? "" : pkg + ".") + String.join(".", owners); }
        String text(Tree n) {
            if (n == null) return "";
            long a = positions.getStartPosition(unit, n), b = positions.getEndPosition(unit, n);
            return a >= 0 && b >= a && b <= source.length() ? source.substring((int)a, (int)b) : n.toString();
        }
        Map<String,Object> event(String kind, Tree n) {
            long a = positions.getStartPosition(unit,n), b = positions.getEndPosition(unit,n);
            return map("kind",kind,"owner",owner(),"method",method,
                "line", a < 0 ? 1 : unit.getLineMap().getLineNumber(a),
                "end_line", b < 0 ? 1 : unit.getLineMap().getLineNumber(Math.max(a,b-1)));
        }
        void out(Map<String,Object> m) { System.out.println(json(m)); }
        List<String> args(List<? extends ExpressionTree> a) {
            List<String> result = new ArrayList<String>();
            for (Tree t : a) result.add(t.toString());
            return result;
        }
        List<Object> guards(Tree node) {
            List<Object> result = new ArrayList<Object>();
            long position = positions.getStartPosition(unit,node);
            for (TreePath p = getCurrentPath().getParentPath(); p != null; p = p.getParentPath()) {
                if (p.getLeaf() instanceof MethodTree) break;
                if (p.getLeaf() instanceof IfTree) {
                    IfTree i = (IfTree)p.getLeaf();
                    long start = positions.getStartPosition(unit,i.getThenStatement());
                    long end = positions.getEndPosition(unit,i.getThenStatement());
                    boolean inThen = position >= start && position < end;
                    Tree other = i.getElseStatement();
                    boolean inElse = other != null && position >= positions.getStartPosition(unit,other)
                        && position < positions.getEndPosition(unit,other);
                    if (inThen || inElse) result.add(map("condition",i.getCondition().toString(),"negated",inElse));
                }
                if (p.getLeaf() instanceof CaseTree && p.getParentPath().getLeaf() instanceof SwitchTree) {
                    CaseTree c = (CaseTree)p.getLeaf();
                    SwitchTree s = (SwitchTree)p.getParentPath().getLeaf();
                    List<String> labels = new ArrayList<String>();
                    for (CaseTree other : s.getCases())
                        if (other.getExpression() != null) labels.add(other.getExpression().toString());
                    List<String> entries = new ArrayList<String>();
                    for (CaseTree other : s.getCases()) {
                        entries.add(other.getExpression()==null?"default":other.getExpression().toString());
                        if (other == c) break;
                        if (other.getStatements()!=null && !other.getStatements().isEmpty()) entries.clear();
                    }
                    result.add(map("kind","switch","condition",s.getExpression().toString(),"negated",false,
                        "switch_start",positions.getStartPosition(unit,s),
                        "label",c.getExpression()==null?"default":c.getExpression().toString(),
                        "labels",labels,"entry_labels",entries,"fallthrough",switchFallsThrough(s)));
                }
            }
            Collections.reverse(result); return result;
        }
        @Override public Void visitClass(ClassTree n, Void p) {
            String previous = method;
            String name = n.getSimpleName().toString();
            if (name.isEmpty()) {
                String key = owner() + "#" + method;
                int count = anonymous.containsKey(key) ? anonymous.get(key) + 1 : 1;
                anonymous.put(key,count);
                String enclosing = method == null ? "initializer" : method.substring(method.indexOf('#') + 1);
                name = "<anonymous:" + enclosing + ":" + count + ">";
            }
            owners.addLast(name); method = null;
            Map<String,Object> m = event("class",n);
            List<String> interfaces = new ArrayList<String>();
            for (Tree t : n.getImplementsClause()) interfaces.add(t.toString());
            m.putAll(map("name",name,"package",pkg,"imports",imports,"class_kind",n.getKind().name(),
                "extends",n.getExtendsClause() == null ? "" : n.getExtendsClause().toString(),
                "implements",interfaces,"annotations",annotations(n.getModifiers())));
            out(m); super.visitClass(n,p); owners.removeLast(); method = previous; return null;
        }
        @Override public Void visitMethod(MethodTree n, Void p) {
            String old = method;
            List<Object> params = new ArrayList<Object>(); List<String> types = new ArrayList<String>();
            for (VariableTree v : n.getParameters()) {
                types.add(v.getType().toString());
                params.add(map("name",v.getName().toString(),"type",v.getType().toString(),"annotations",annotations(v.getModifiers())));
            }
            method = owner() + "#" + n.getName() + "(" + String.join(",",types) + ")";
            Map<String,Object> m = event("method",n);
            m.putAll(map("name",n.getName().toString(),"parameters",params,
                "return_type",n.getReturnType() == null ? owner() : n.getReturnType().toString(),
                "annotations",annotations(n.getModifiers()),"modifiers",n.getModifiers().getFlags().toString(),
                "body",text(n.getBody()),"source",text(n),"has_body",n.getBody()!=null));
            out(m); super.visitMethod(n,p); method = old; return null;
        }
        @Override public Void visitVariable(VariableTree n, Void p) {
            Tree parent = getCurrentPath().getParentPath().getLeaf();
            if (!(parent instanceof MethodTree)) {
                Map<String,Object> m = event(method == null ? "field" : "variable",n);
                m.putAll(map("name",n.getName().toString(),"type",n.getType() == null ? "" : n.getType().toString(),
                    "initializer",n.getInitializer() == null ? "" : n.getInitializer().toString(),
                    "annotations",annotations(n.getModifiers()),"source",text(n),"guards",guards(n)));
                if (n.getInitializer() instanceof NewClassTree)
                    m.put("constructor_arguments",args(((NewClassTree)n.getInitializer()).getArguments()));
                out(m);
            }
            return super.visitVariable(n,p);
        }
        @Override public Void visitMethodInvocation(MethodInvocationTree n, Void p) {
            if (method != null) {
                ExpressionTree select = n.getMethodSelect();
                String name = select.toString(), receiver = "this";
                if (select instanceof MemberSelectTree) {
                    name = ((MemberSelectTree)select).getIdentifier().toString();
                    receiver = ((MemberSelectTree)select).getExpression().toString();
                }
                Map<String,Object> m = event("call",n);
                m.putAll(map("name",name,"receiver",receiver,"arguments",args(n.getArguments()),
                    "expression",n.toString(),"guards",guards(n),
                    "usage",getCurrentPath().getParentPath().getLeaf().getKind().name()));
                out(m);
            }
            return super.visitMethodInvocation(n,p);
        }
        @Override public Void visitNewClass(NewClassTree n, Void p) {
            if (method != null) {
                Map<String,Object> m = event("construct",n);
                m.putAll(map("type",n.getIdentifier().toString(),"arguments",args(n.getArguments()),"expression",n.toString(),"guards",guards(n)));
                out(m);
            }
            return super.visitNewClass(n,p);
        }
        @Override public Void visitAssignment(AssignmentTree n, Void p) {
            if (method != null) {
                Map<String,Object> m = event("assignment",n);
                m.putAll(map("target",n.getVariable().toString(),"expression",n.getExpression().toString(),"guards",guards(n))); out(m);
            }
            return super.visitAssignment(n,p);
        }
        void fieldRead(ExpressionTree n, String receiver, String name) {
            if (method == null) return;
            Tree parent = getCurrentPath().getParentPath().getLeaf();
            if (parent instanceof AssignmentTree && ((AssignmentTree)parent).getVariable() == n) return;
            if (parent instanceof MethodInvocationTree && ((MethodInvocationTree)parent).getMethodSelect() == n) return;
            if (parent instanceof VariableTree && ((VariableTree)parent).getType() == n) return;
            if (parent instanceof ParameterizedTypeTree || parent instanceof AnnotationTree) return;
            Map<String,Object> m = event("field_read",n);
            m.putAll(map("name",name,"receiver",receiver,"expression",n.toString(),"guards",guards(n))); out(m);
        }
        @Override public Void visitIdentifier(IdentifierTree n, Void p) {
            fieldRead(n,"",n.getName().toString()); return super.visitIdentifier(n,p);
        }
        @Override public Void visitMemberSelect(MemberSelectTree n, Void p) {
            fieldRead(n,n.getExpression().toString(),n.getIdentifier().toString());
            return super.visitMemberSelect(n,p);
        }
        @Override public Void visitReturn(ReturnTree n, Void p) {
            Map<String,Object> m = event("return",n);
            m.putAll(map("expression",n.getExpression()==null?"":n.getExpression().toString(),"guards",guards(n))); out(m);
            return super.visitReturn(n,p);
        }
        @Override public Void visitIf(IfTree n, Void p) {
            Map<String,Object> m = event("branch",n);
            m.putAll(map("condition",n.getCondition().toString(),"then",n.getThenStatement().toString(),
                "else",n.getElseStatement()==null?"":n.getElseStatement().toString(),"guards",guards(n))); out(m);
            return super.visitIf(n,p);
        }
        @Override public Void visitSwitch(SwitchTree n, Void p) {
            Map<String,Object> m = event("switch",n); m.put("expression",n.getExpression().toString());
            List<String> values = new ArrayList<String>();
            for (CaseTree c : n.getCases()) values.add(c.getExpression()==null?"default":c.getExpression().toString());
            m.put("cases",values); out(m);
            if (switchFallsThrough(n)) boundary(n,"switch_fallthrough");
            return super.visitSwitch(n,p);
        }
        boolean switchFallsThrough(SwitchTree n) {
            List<? extends CaseTree> cases = n.getCases();
            for (int i=0; i+1<cases.size(); i++) {
                List<? extends StatementTree> statements = cases.get(i).getStatements();
                if (statements == null || statements.isEmpty()) continue;
                Tree.Kind last = statements.get(statements.size()-1).getKind();
                if (last != Tree.Kind.BREAK && last != Tree.Kind.RETURN && last != Tree.Kind.THROW) return true;
            }
            return false;
        }
        @Override public Void visitForLoop(ForLoopTree n, Void p) { boundary(n,"loop"); return super.visitForLoop(n,p); }
        @Override public Void visitEnhancedForLoop(EnhancedForLoopTree n, Void p) { boundary(n,"loop"); return super.visitEnhancedForLoop(n,p); }
        @Override public Void visitWhileLoop(WhileLoopTree n, Void p) { boundary(n,"loop"); return super.visitWhileLoop(n,p); }
        @Override public Void visitMemberReference(MemberReferenceTree n, Void p) { boundary(n,"method_reference"); return super.visitMemberReference(n,p); }
        void boundary(Tree n, String reason) {
            Map<String,Object> m=event("boundary",n); m.putAll(map("reason",reason,"expression",n.toString())); out(m);
        }
    }
    public static void main(String[] args) throws Exception {
        System.setOut(new PrintStream(System.out,true,"UTF-8"));
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) throw new IllegalStateException("JDK compiler required");
        try (StandardJavaFileManager manager = compiler.getStandardFileManager(null,Locale.ROOT,StandardCharsets.UTF_8)) {
            for (String filename : Files.readAllLines(Paths.get(args[0]),StandardCharsets.UTF_8)) {
                if (filename.isEmpty()) continue;
                DiagnosticCollector<JavaFileObject> diag = new DiagnosticCollector<JavaFileObject>();
                JavacTask task = (JavacTask)compiler.getTask(null,manager,diag,Arrays.asList("-source","8","-proc:none"),null,
                    manager.getJavaFileObjects(new File(filename)));
                Iterable<? extends CompilationUnitTree> units = task.parse();
                boolean error = false;
                for (Diagnostic<?> d : diag.getDiagnostics()) if (d.getKind()==Diagnostic.Kind.ERROR) error=true;
                System.out.println(json(map("kind","file","file",filename,"parse_error",error)));
                if (!error) for (CompilationUnitTree unit : units)
                    new Scan(unit,Trees.instance(task),new String(Files.readAllBytes(Paths.get(filename)),StandardCharsets.UTF_8)).scan(unit,null);
            }
        }
    }
}
