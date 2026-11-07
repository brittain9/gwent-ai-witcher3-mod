import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instruction;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.ScriptInfo;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import java.io.FileInputStream;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Reject eager reads of same-script class slots before AVM2 installs them. */
class CheckAs3Initialization {
    private static String name(ABC abc, int multiname) {
        return abc.constants.getString(abc.constants.getMultiname(multiname).name_index);
    }

    private static String identity(ABC abc, int multiname) {
        return abc.constants.getMultiname(multiname).toString(abc.constants, List.of());
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("Expected one compiled SWF path");
        }
        int checked = 0;
        try (FileInputStream input = new FileInputStream(args[0])) {
            SWF swf = new SWF(input, false);
            for (ABCContainerTag tag : swf.getAbcList()) {
                ABC abc = tag.getABC();
                for (ScriptInfo script : abc.script_info) {
                    MethodBody body = abc.findBody(script.init_index);
                    if (body == null) continue;
                    Set<String> siblings = new HashSet<>();
                    for (AVM2Instruction instruction : body.getCode().code) {
                        if (instruction.definition.instructionName.equals("newclass")) {
                            siblings.add(identity(abc, abc.instance_info.get(instruction.operands[0]).name_index));
                        }
                    }
                    Set<String> installed = new HashSet<>();
                    for (AVM2Instruction instruction : body.getCode().code) {
                        String operation = instruction.definition.instructionName;
                        if (operation.equals("initproperty")) {
                            String slot = identity(abc, instruction.operands[0]);
                            if (siblings.contains(slot)) installed.add(slot);
                        } else if (operation.equals("newclass")) {
                            int classIndex = instruction.operands[0];
                            String owner = name(abc, abc.instance_info.get(classIndex).name_index);
                            if (!owner.startsWith("Gwai")) continue;
                            checked++;
                            MethodBody initializer = abc.findBody(abc.class_info.get(classIndex).cinit_index);
                            if (initializer == null) {
                                throw new IllegalStateException(owner + " has no static initializer body");
                            }
                            for (AVM2Instruction read : initializer.getCode().code) {
                                if (!read.definition.instructionName.equals("getlex")) continue;
                                String dependency = name(abc, read.operands[0]);
                                String key = identity(abc, read.operands[0]);
                                if (siblings.contains(key) && !installed.contains(key)) {
                                    throw new IllegalStateException(owner + " reads " + dependency
                                        + " before its class is installed");
                                }
                            }
                        }
                    }
                }
            }
        }
        if (checked == 0) throw new IllegalStateException("No Gwai class initializers checked");
        System.out.println("PASS: " + checked + " helper class initializers");
    }
}
